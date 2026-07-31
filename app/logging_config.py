"""Structured logging for the Flask port: one deterministic format, one git-ignored file.

What this module is, and the two lines it descends from
------------------------------------------------------
The whole of this module traces to a two-line block of the pre-migration ``.gitignore``::

    5: # Log file
    6: *.log

That block is the *only* evidence the source Java/Maven project produced logs at all, which
is why Agent Action Plan (AAP) section 0.4.1 binds this file to ``[.gitignore:L6]`` and why
the ``.log`` suffix of the destination is a **parity requirement** rather than a convention.
AAP section 0.4.1 additionally requires that the updated ignore file keep
``configuration.properties`` ``[.gitignore:L3]`` and ``*.log`` ``[.gitignore:L6]`` verbatim,
so a destination ending in ``.log`` stays untracked with no new ignore rule and no
negation - provable with ``git check-ignore -v logs/testinium-qa.log``, which reports
``.gitignore:6:*.log``.

This module is the sole realization of the AAP section 0.7 enterprise baseline item B9 --
"structured logging rather than print statements, to a git-ignored path". Eleven modules
across ``app/``, ``tests/support/`` and ``scripts/`` state in their own docstrings that
handlers, levels and formats belong exclusively here; each of them merely obtains
``logging.getLogger(__name__)`` and emits. Nothing in the tree prints.

Two hard constraints on the destination
---------------------------------------
1. **It must end in** ``.log``. Only then does the preserved ``*.log`` pattern
   ``[.gitignore:L6]`` cover it. A destination that does not is honoured - configuration is
   never overridden here - but it is reported with a warning naming the ignore line, because
   the file would then be *trackable* and could be committed by accident.
2. **It must not sit inside** ``target/``. Everything under the retained Maven artifact root
   is ephemeral by design: the port of ``mvn clean`` (``make clean``, and ``make test``,
   which begins with it) wipes the tree on every run, and AAP section 0.6 enumerates exactly
   what belongs there - ``cucumber.json``, ``cucumber-reports.html``, ``rerun.txt``,
   ``cucumber/``, ``screenshots/``, ``error-shots/`` and ``surefire-reports/``. No log file
   appears in that list. An application log is not a per-run test artifact, so putting it
   there would destroy it precisely when someone needs it. A destination inside ``target/``
   is likewise honoured, with a warning.

Both are satisfied by the committed default, ``logs/testinium-qa.log``
(``app.config.DEFAULT_LOG_FILE``), which this module never has to override.

Where the settings come from
----------------------------
``app/config.py`` owns the five-rung precedence chain of AAP section 0.3.1 - explicit
constructor argument, environment variable, ``.env``, ``configuration.properties``,
hard-coded source default - and is the single load point. This module therefore reads
**nothing** itself: no ``os.environ``, no ``.env``, no properties file. It consumes exactly
two resolved settings, both documented in the committed ``.env.example`` (``LOG_LEVEL``,
``LOG_FILE``) and in ``configuration.properties.example`` (``log.level``, ``log.file``) with
identical defaults, and it takes them off the Flask application's ``config`` mapping or from
explicit keyword arguments. It deliberately does not import :mod:`app.config`, which keeps
the factory's import order trivial: the factory imports both, in either order, with no
possibility of a cycle.

No third configuration key is introduced. A format switch, for instance, would need a key in
all three committed configuration surfaces, and AAP section 0.8 is binding - "No feature may
be dropped, and none may be added". Hence one deterministic text format
(:data:`RECORD_FORMAT`) and no JSON-lines alternative.

Why a plain file handler and never a rotating one
-------------------------------------------------
:class:`logging.handlers.RotatingFileHandler` names its backups ``testinium-qa.log.1``,
``testinium-qa.log.2`` and so on. Those names do **not** end in ``.log``, so they escape the
preserved ``*.log`` pattern ``[.gitignore:L6]`` and would surface as untracked, committable
files - defeating the single requirement this module exists to satisfy. Rotation is therefore
excluded on purpose, not by omission. A deployment that needs rotation should rotate
externally (``logrotate``, the container runtime, or the platform's log driver), where the
naming stays outside this repository.

Who owns which stream
---------------------
* **This module** owns the root logger's handlers: one stream handler on ``sys.stderr`` and,
  when a destination is configured, one UTF-8 file handler. Handlers on the *root* logger is
  what makes application records, Flask records and Werkzeug's request log share one format.
* **``gunicorn.conf.py``** owns gunicorn's own logs: ``accesslog`` (stdout), ``errorlog``
  (stderr), ``loglevel`` - which falls back to the same ``LOG_LEVEL`` key so the two track
  each other - and ``capture_output``. Gunicorn sets ``propagate = False`` on its
  ``gunicorn.error`` and ``gunicorn.access`` loggers, so its records never reach the handlers
  installed here and nothing is written twice. This module configures no ``gunicorn.*``
  logger; :data:`GUNICORN_LOGGER_NAMES` exists to name that boundary, not to cross it.
* **Flask's** own ``default_handler`` is *removed* from the application logger. Flask attaches
  it the first time ``app.logger`` is touched if no ancestor already has a handler; had the
  factory touched the logger before calling in here, every application record would then be
  emitted twice - once by that handler and once through propagation to the root handlers.
  Removal is Flask's own documented remedy and is idempotent.

Idempotence
-----------
``create_app()`` runs many times in one process: ``tests/conftest.py`` builds an isolated
application per test, and every ``-n logical`` xdist worker builds its own. Repeated
configuration must therefore never stack handlers, or one message would be written N times -
which would, among other things, blur the exit-code-5 evidence that validation criterion V6
depends on. :func:`configure_logging` removes and closes only the handlers *this module*
installed, identified by a private marker base class, and installs fresh ones. Handlers
belonging to anyone else - above all pytest's ``caplog`` capture handler on the root logger -
are left untouched, which a blanket ``handlers.clear()`` would have destroyed.

Level policy
------------
The configured level is applied exactly to the application logger hierarchy (``app`` and,
when a Flask instance is supplied, its own logger name) and to ``werkzeug``. The **root**
logger's level is only ever *lowered*, never raised: ``min(level, WARNING)``. Raising it
would silence every third-party library below Python's own default, which is forbidden. This
costs nothing, because propagation consults the *originating* logger's effective level and
each handler's level, never an ancestor logger's level - so ``app.*`` records at ``INFO``
still reach the root handlers while ``LOG_LEVEL=ERROR`` leaves library defaults alone.

Secrets
-------
Baseline B5 forbids logging credentials. :func:`redact` is applied by
:class:`StructuredFormatter` to the *rendered* line, so nothing unredacted is ever written,
while ``record.getMessage()`` stays intact for assertions and for ``caplog``. The clone URL
of ``[Jenkins:L3]``, ``https://github.com/BalamiRR/Upgenix-QA.git``, is public and survives
verbatim - it carries no user-info component - whereas an operator-configured
``https://<token>@github.com/...`` has its user-info replaced. The e-mail addresses in the
feature file's ``Examples`` tables (``salesmanager7@info.com``) are untouched, because a
user-info match requires a preceding ``scheme://``.

Message fidelity
----------------
Records are rendered, never rewritten. No truncation, no normalization, no case folding, no
whitespace stripping. That matters concretely: the French assertion literal
``Veuillez renseigner ce champ.`` ``[README.md:L135]`` (preserved defect D5) must survive
byte for byte, trailing period included, and the U+2013 EN DASH of the two documented report
commands ``[README.md:L157, L161]`` is real non-ASCII content in this repository. Every file
handler is therefore constructed with an explicit ``encoding="utf-8"`` (baseline B7) and
strict error handling, so an encoding problem surfaces instead of silently mangling text.

What this module deliberately does not do
-----------------------------------------
* It configures nothing at **import** time. A module-level ``logging.basicConfig`` would fire
  on import, reach into every application in the process and defeat the per-application
  isolation the factory exists to provide. Importing this module has no side effects at all:
  no handler, no level change, no file.
* It builds no Flask application (baseline B3) and registers no blueprint or error handler.
* It never creates ``target/`` or any subdirectory of it. AAP section 0.6 assigns that to
  exactly three owners - ``app/utils/paths.py``, the ``Makefile`` test target and
  ``tests/conftest.py`` - and a fourth would diverge from the specification that validation
  criterion V7 grades. Only the *log file's own parent directory* is created, through
  :func:`app.utils.paths.ensure_parent_directory`, which never raises.
* It adds no third-party logging distribution. The runtime manifest of AAP section 0.5.1 is
  exhaustive and contains none, and the deployed container installs ``requirements.txt``
  alone, so every structured-logging facade, JSON-formatter package, cloud log client, log
  shipper, syslog target, metrics exporter and APM agent is excluded. Only
  :mod:`logging` from the standard library is used.
* It filters no warnings and never calls ``logging.disable()``. ``pytest.ini`` owns
  ``filterwarnings`` (baseline B10), explicitly rather than wholesale, so that the registered
  Gherkin markers of validation criterion V10 cannot hide behind a blanket filter.

Layering (AAP Rule T7, baseline B4)
-----------------------------------
The direction is ``api -> services -> reporting -> utils``, and nothing under ``app/`` may
import from ``tests/``. This module imports the standard library, ``flask`` (the application
type and its documented default handler) and ``app.utils.paths`` - the bottom layer - and
nothing else. It must never import ``app.api``, ``app.web``, ``app.services`` or
``app.reporting``, nor anything under ``tests/`` or ``scripts/``, nor any harness-only
distribution (``pytest``, ``pytest_bdd``, ``selenium``, ``webdriver_manager``, ``faker``),
because ``wsgi.py`` -> ``app/__init__.py`` -> this module has to import cleanly in a
container built from ``requirements.txt`` alone.

Observability contract for the ported pipeline
----------------------------------------------
The three stages of the Groovy scripted pipeline ``[Jenkins:L1-L17]`` become three service
modules, each with its own logger under the ``app.services`` hierarchy. :data:`STAGE_LOGGER_NAMES`
maps the stage names - spelled byte-identically to the source, because that spelling is part
of the pipeline's observable contract - onto those logger names, and :func:`stage_logger`
resolves one. Three behaviours are otherwise invisible and must appear in the log:

* ``'Clone code'`` ``[Jenkins:L2-L4]``: the already-cloned path (fetch and reset rather than
  fail), the explicit subprocess timeout, and the fact that the URL is passed as an argument
  list and never interpolated into a shell string.
* ``'Run tests'`` ``[Jenkins:L6-L11]``: the raw pytest exit code **and** the verdict it maps
  to. ``0`` success; ``1`` non-failing, the direct port of
  ``<testFailureIgnore>true</testFailureIgnore>`` ``[pom.xml:L25]``; ``2``, ``3`` and ``4``
  hard failures; and ``5`` - every scenario deselected by the preserved ``-m "LogOut"`` filter
  (defect D2, from ``tags = "@LogOut"`` ``[README.md:L87]``) - a **successful zero-scenario
  run**. Logging "zero scenarios selected, reported as SUCCESS" is what makes validation
  criterion V6 auditable instead of mystifying.
* ``'Generate report'`` ``[Jenkins:L13-L15]``: that publication happened **even after a failed
  test stage** (defect D3, validation criterion V12), from the finally-equivalent path in
  ``pipeline_service.py``.

Usage
-----
::

    from app.logging_config import configure_logging

    def create_app(config_name: str | None = None) -> Flask:
        app = Flask(__name__)
        app.config.from_object(load_config(config_name))
        configure_logging(app)          # once, inside the factory
        ...

    # Anywhere else - obtain a logger, never configure one:
    _LOGGER = logging.getLogger(__name__)
"""

import logging
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final, TextIO

from flask import Flask
from flask.logging import default_handler as _FLASK_DEFAULT_HANDLER

from app.utils.paths import TARGET_DIR, ensure_parent_directory, to_posix

# The public surface, ASCII-sorted in two groups: constants first, then the callables and the
# formatter class. `configure_logging` is the entry point the application factory calls;
# `init_logging` is a documented alias of the same function, so a caller that reaches for
# either name gets identical behaviour (`app/utils/paths.py` establishes the same idiom for
# `ensure_target_layout`). Nothing private is exported: the managed-handler marker classes are
# an implementation detail of idempotence.
__all__ = [
    "APPLICATION_LOGGER_NAME",
    "CLONE_STAGE_LOGGER_NAME",
    "DATE_FORMAT",
    "FALLBACK_LEVEL",
    "FALLBACK_LEVEL_NAME",
    "GUNICORN_LOGGER_NAMES",
    "IGNORED_LOG_SUFFIX",
    "LOG_FILE_CONFIG_KEY",
    "LOG_LEVEL_CONFIG_KEY",
    "PIPELINE_LOGGER_NAME",
    "RECORD_FORMAT",
    "REDACTION_PLACEHOLDER",
    "REPORT_STAGE_LOGGER_NAME",
    "STAGE_LOGGER_NAMES",
    "TEST_STAGE_LOGGER_NAME",
    "WERKZEUG_LOGGER_NAME",
    "StructuredFormatter",
    "application_logger",
    "configure_logging",
    "init_logging",
    "is_configured",
    "redact",
    "reset_logging",
    "resolve_level",
    "stage_logger",
]

# This module's own logger, obtained exactly like every other module's. It is used only for
# the diagnostics below - the routine confirmation at DEBUG, and the anomalies at WARNING -
# and it is deliberately obtained rather than configured, even here.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


# =============================================================================
# SECTION 1 -- The record format.
#
# "Structured" means every record, from every logger in the process, is rendered
# through one fixed formatter: a timestamp, a level, the logger name, the process
# id and the message. Nothing is optional and nothing varies by caller, so the
# output is greppable and diffable, and a log line from an xdist worker or a
# gunicorn worker is attributable to its process.
# =============================================================================

RECORD_FORMAT: Final[str] = "%(asctime)s %(levelname)-8s %(name)s [pid=%(process)d] %(message)s"
"""The single record layout, applied uniformly to every handler this module installs.

The logger name carries the module (``app.services.test_runner_service``), so ``%(module)s``
and ``%(funcName)s`` would be redundant. ``%(process)d`` is present because this application
runs multi-process in both of its parallel modes - gunicorn workers and the ``-n logical``
xdist workers that port ``<parallel>methods</parallel>`` with
``<useUnlimitedThreads>true</useUnlimitedThreads>`` ``[pom.xml:L22-L23]``.
"""

DATE_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S%z"
"""ISO 8601 timestamps with an explicit UTC offset, so records sort lexicographically.

An explicit ``datefmt`` also makes the rendering deterministic across platforms and
locales, which matters for a log that is compared in tests.
"""


# =============================================================================
# SECTION 2 -- The two settings this module consumes, and nothing else.
#
# Both keys are resolved by app/config.py through the five-rung chain and are
# documented in .env.example (LOG_LEVEL, LOG_FILE) and in
# configuration.properties.example (log.level, log.file) with the same defaults.
# =============================================================================

LOG_LEVEL_CONFIG_KEY: Final[str] = "LOG_LEVEL"
"""Application-config key holding the standard-library level name. Default ``INFO``."""

LOG_FILE_CONFIG_KEY: Final[str] = "LOG_FILE"
"""Application-config key holding the log destination. Default ``logs/testinium-qa.log``."""

FALLBACK_LEVEL_NAME: Final[str] = "INFO"
"""Level of last resort, used only when a caller supplies no configuration at all.

The authoritative default lives once, in ``app.config.DEFAULT_LOG_LEVEL``, and reaches this
module through the application config. This constant exists so that configuring a bare
``Flask(__name__)`` - a fixture, a smoke check - still produces useful output instead of
falling back to the ``WARNING`` of an unconfigured logging system.
"""

FALLBACK_LEVEL: Final[int] = logging.INFO
"""Numeric form of :data:`FALLBACK_LEVEL_NAME`."""

IGNORED_LOG_SUFFIX: Final[str] = ".log"
"""The suffix the preserved ignore pattern ``*.log`` ``[.gitignore:L6]`` matches.

A configured destination that does not end in this is honoured and warned about: the file
would be trackable, and committing an application log is exactly what that ignore line has
prevented since before the migration.
"""


# =============================================================================
# SECTION 3 -- The logger hierarchy.
#
# Every module in the tree obtains `logging.getLogger(__name__)`, so the whole
# application hierarchy hangs off `app`. The factory builds `Flask(__name__)`
# from `app/__init__.py`, which makes Flask's own `app.logger` the *same* logger
# as `logging.getLogger("app")` -- one hierarchy, not two.
# =============================================================================

APPLICATION_LOGGER_NAME: Final[str] = "app"
"""Root of the application hierarchy, and the name of Flask's logger for this package."""

WERKZEUG_LOGGER_NAME: Final[str] = "werkzeug"
"""Werkzeug's logger, which carries the development server's request log.

Werkzeug raises its own level to ``INFO`` on first use when the level is unset; setting it
explicitly here makes the verbosity deterministic and tied to ``LOG_LEVEL`` instead.
"""

GUNICORN_LOGGER_NAMES: Final[tuple[str, ...]] = ("gunicorn.access", "gunicorn.error")
"""Loggers owned by ``gunicorn.conf.py`` and deliberately NOT configured here.

Gunicorn installs its own handlers on these and sets ``propagate = False``, so its records
never reach the handlers installed by this module. The names are published to document that
boundary - and to let a test assert it is respected - not to cross it.
"""

CLONE_STAGE_LOGGER_NAME: Final[str] = "app.services.clone_service"
"""Logger of the module porting stage ``'Clone code'`` ``[Jenkins:L2-L4]``."""

TEST_STAGE_LOGGER_NAME: Final[str] = "app.services.test_runner_service"
"""Logger of the module porting stage ``'Run tests'`` ``[Jenkins:L6-L11]``."""

REPORT_STAGE_LOGGER_NAME: Final[str] = "app.services.report_service"
"""Logger of the module porting stage ``'Generate report'`` ``[Jenkins:L13-L15]``."""

PIPELINE_LOGGER_NAME: Final[str] = "app.services.pipeline_service"
"""Logger of the module porting the ``node { }`` container itself ``[Jenkins:L1-L17]``."""

STAGE_LOGGER_NAMES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "Clone code": CLONE_STAGE_LOGGER_NAME,
        "Run tests": TEST_STAGE_LOGGER_NAME,
        "Generate report": REPORT_STAGE_LOGGER_NAME,
    }
)
"""The three source stage names, mapped to the logger that traces each.

The keys are spelled exactly as ``[Jenkins:L2, L6, L14]`` spells them - capitalisation and
spacing included - because AAP section 0.8 keeps all three stage names byte-identical: "All
three stage names, the platform dispatch, and the publisher invocation ``[Jenkins:L15]`` stay
byte-identical. Only the two command strings change." Read-only, so the mapping cannot drift
at runtime. The ``node { }`` container is not a stage and is therefore absent; its logger is
:data:`PIPELINE_LOGGER_NAME`.
"""


# =============================================================================
# SECTION 4 -- Credential redaction (baseline B5).
#
# Applied to the RENDERED line by the formatter, so nothing unredacted is ever
# written while `record.getMessage()` stays intact for assertions and caplog.
# Every pattern below is deliberately narrow: the strings this port must carry
# verbatim -- the French assertion literal [README.md:L135], the two EN DASH
# report commands [README.md:L157, L161], the public clone URL [Jenkins:L3] and
# the Examples-table e-mail addresses -- must pass through untouched.
# =============================================================================

REDACTION_PLACEHOLDER: Final[str] = "***REDACTED***"
"""What a redacted value is replaced with. Cannot match any provider's token shape."""

# A URL's user-info component: `scheme://user:password@host`. The whole component goes, not
# just the password, because a forge token is as often placed in the user position
# (`https://ghp_.../repo.git`) as in the password position. A preceding `scheme://` is
# REQUIRED, which is what leaves a bare e-mail address such as `salesmanager7@info.com` -
# real data in the feature file's Examples tables [README.md:L140-L142, L147-L148] - alone,
# and leaves the credential-free clone URL `https://github.com/BalamiRR/Upgenix-QA.git`
# [Jenkins:L3] byte-identical, because it has no user-info component at all.
_URL_CREDENTIALS_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)(?P<userinfo>[^\s/@]+)@"
)

# An HTTP authorization credential, with its optional `Authorization:` or `Authorization=`
# prefix consumed in the same match so the scheme keyword survives and only the credential
# goes. Matched BEFORE the assignment pattern below, which would otherwise stop at the
# keyword and leave the credential itself in the line.
_AUTHORIZATION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>(?:\bauthorization\s*[:=]\s*)?\b(?:bearer|basic)\s+)"
    r"(?P<value>[A-Za-z0-9._~+/=\-]{8,})",
    re.IGNORECASE,
)

# The key words that make an assignment's value a secret. Matched case-insensitively and as
# part of a longer identifier, so `SECRET_KEY`, `db.password`, `X-Api-Key` and
# `access_token` all qualify.
_SECRET_KEY_WORDS: Final[str] = (
    r"passwo?r?d|passwd|pwd|secret|token|credentials?|"
    r"api[_.\-]?key|access[_.\-]?key|private[_.\-]?key|session[_.\-]?key|authorization"
)

# `key=value`, `key: value`, `"key": "value"`. The value stops at the first delimiter, so a
# surrounding structure survives and only the value goes. Two negative lookaheads keep the
# pattern honest: it must not consume an authorization scheme keyword (already handled
# above), and a value that is already the placeholder is left as it is, which makes redaction
# idempotent. Note that no ordinary message in this project matches - `log.file=...`,
# `tag_expression='LogOut'` and `-Dcucumber.options="--plugin html:target/..."` carry none of
# the key words - while `SECRET_KEY: default` does, and should.
#
# The trailing conditional group is what keeps the rewritten line well formed. Because
# `value` excludes quote characters it stops *before* a closing quote, so without consuming
# that quote here the substitution - which re-emits `\g<quote>` as the closer - would leave
# the original one stranded and render `api_key="***REDACTED***""` with a doubled quote. The
# conditional consumes the closer only when an opening `quote` was actually matched, and
# tolerates its absence (`?`) so a mismatched pair such as `api_key='a"b'` still redacts
# rather than falling through unredacted.
_SECRET_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?P<key>[A-Za-z0-9_.\-]*(?:{_SECRET_KEY_WORDS})[A-Za-z0-9_.\-]*)"
    r"(?P<keyquote>[\"']?)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>(?!(?:bearer|basic)\b)(?!\*\*\*REDACTED\*\*\*)[^\s,;)\]}\"']+)"
    r"(?(quote)(?P=quote)?|)",
    re.IGNORECASE,
)

# Provider-shaped tokens, recognised by prefix so they are caught even when they appear bare
# in a message with no key beside them. Each alternative carries a minimum length so ordinary
# prose cannot trip it.
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:"
    r"(?:sk_live_|sk_test_|pk_live_|pk_test_|rk_live_|github_pat_|ghp_|gho_|ghs_|ghu_|ghr_|"
    r"xox[abprs]-|AIza|AKIA|ASIA)[A-Za-z0-9_\-]{8,}"
    r"|sk-[A-Za-z0-9_\-]{16,}"
    r")"
)

# A compact JSON Web Token: three base64url segments separated by dots, the first of which
# begins with the `{"` that `eyJ` encodes.
_JWT_RE: Final[re.Pattern[str]] = re.compile(
    r"\beyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}"
)

# A PEM private key, whole block when the END marker is present and just the BEGIN marker
# when a single record carries only the opening line.
_PRIVATE_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"-----BEGIN[A-Z ]*PRIVATE KEY-----(?:[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----)?"
)


def redact(text: str) -> str:
    """Replace anything credential-shaped in *text* with :data:`REDACTION_PLACEHOLDER`.

    Applied by :class:`StructuredFormatter` to every rendered line, so no handler installed by
    this module can write an unredacted credential (baseline B5: "never hard-coded secrets").
    Exported so that a service can also redact a value it is about to put into a message or an
    HTTP response, and so that the behaviour is directly assertable.

    The order of the passes matters. The private-key block goes first because it may contain
    anything; the URL user-info next; then the authorization credential, whose optional
    ``Authorization:`` prefix must be consumed before the generic assignment pass sees it;
    then generic secret assignments; and finally the bare provider-token and JWT shapes.

    Args:
        text: A rendered log line, or any string bound for a log or a response.

    Returns:
        *text* with every recognised credential replaced. The function is idempotent -
        ``redact(redact(value)) == redact(value)`` - and it is a no-op for text that contains
        nothing credential-shaped, which is why the strings this port must preserve verbatim
        pass through it unchanged:

        * ``Veuillez renseigner ce champ.`` ``[README.md:L135]``, defect D5, trailing period
          included;
        * the U+2013 EN DASH of ``[README.md:L157, L161]``;
        * ``https://github.com/BalamiRR/Upgenix-QA.git`` ``[Jenkins:L3]``, which has no
          user-info component;
        * ``salesmanager7@info.com`` and the other Examples-table addresses, which have no
          preceding ``scheme://``.

    Examples:
        The forge token below is deliberately a placeholder, not a credential shape any
        provider would ever issue::

        >>> redact("cloning https://ghp_NOT_A_REAL_TOKEN_PLACEHOLDER@example.invalid/qa.git")
        'cloning https://***REDACTED***@example.invalid/qa.git'
        >>> redact("Veuillez renseigner ce champ.")
        'Veuillez renseigner ce champ.'
    """
    redacted = _PRIVATE_KEY_RE.sub(REDACTION_PLACEHOLDER, text)
    redacted = _URL_CREDENTIALS_RE.sub(rf"\g<scheme>{REDACTION_PLACEHOLDER}@", redacted)
    redacted = _AUTHORIZATION_RE.sub(rf"\g<prefix>{REDACTION_PLACEHOLDER}", redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(
        rf"\g<key>\g<keyquote>\g<separator>\g<quote>{REDACTION_PLACEHOLDER}\g<quote>", redacted
    )
    redacted = _TOKEN_RE.sub(REDACTION_PLACEHOLDER, redacted)
    return _JWT_RE.sub(REDACTION_PLACEHOLDER, redacted)


# =============================================================================
# SECTION 5 -- The formatter.
# =============================================================================


class StructuredFormatter(logging.Formatter):
    """The one formatter every handler installed by this module uses.

    Renders :data:`RECORD_FORMAT` with :data:`DATE_FORMAT` and then, unless redaction is
    switched off, passes the result through :func:`redact`. Redacting the *rendered* line
    rather than mutating the record is deliberate on three counts: it covers the message, its
    interpolated arguments and any exception traceback in one pass; it cannot corrupt a record
    that other handlers - pytest's ``caplog`` capture handler, for instance - still have to
    render; and it guarantees that nothing unredacted reaches a stream or a file.

    Message text is otherwise untouched. Nothing is truncated, normalised, case-folded or
    stripped, because ``Veuillez renseigner ce champ.`` ``[README.md:L135]`` must survive byte
    for byte, trailing period included, and the U+2013 EN DASH of ``[README.md:L157, L161]``
    is real content in this repository.
    """

    def __init__(
        self,
        fmt: str = RECORD_FORMAT,
        datefmt: str = DATE_FORMAT,
        *,
        redact_secrets: bool = True,
    ) -> None:
        """Build the formatter.

        Args:
            fmt: Record layout. Defaults to :data:`RECORD_FORMAT`, which is what makes the
                output uniform; overriding it is for tests, not for deployments.
            datefmt: Timestamp layout. Defaults to :data:`DATE_FORMAT`.
            redact_secrets: Whether to apply :func:`redact` to the rendered line. Defaults to
                ``True`` and should stay that way outside a test that asserts the difference.
        """
        super().__init__(fmt=fmt, datefmt=datefmt, style="%")
        self.redact_secrets = redact_secrets

    def format(self, record: logging.LogRecord) -> str:
        """Render *record*, then redact the result.

        Args:
            record: The record to render. It is never mutated.

        Returns:
            The rendered line, with credentials replaced when redaction is enabled.
        """
        rendered = super().format(record)
        if self.redact_secrets:
            return redact(rendered)
        return rendered


# =============================================================================
# SECTION 6 -- Managed handlers: how idempotence is achieved.
#
# `configure_logging` must be safe to call once per application, and the factory
# builds one application per test and one per xdist worker. Handlers created here
# therefore carry a marker in their TYPE, so a repeat call can remove exactly its
# own handlers and nobody else's. A blanket `logger.handlers.clear()` would have
# torn out pytest's caplog capture handler and broken every test that asserts on
# log records.
# =============================================================================


class _ManagedHandler(logging.Handler):
    """Marker base class: a handler installed by this module and removable by it.

    Deliberately empty. Identity is established by ``isinstance`` rather than by an attribute
    poked onto a stock handler instance, which keeps the check type-safe and keeps this
    module's ownership visible in ``logging.getLogger().handlers`` under inspection.
    """


class _ManagedStreamHandler(_ManagedHandler, logging.StreamHandler):
    """The stream handler installed on the root logger."""


class _ManagedFileHandler(_ManagedHandler, logging.FileHandler):
    """The UTF-8 file handler installed on the root logger when a destination is configured."""


def _prune_managed_handlers(logger: logging.Logger) -> int:
    """Remove and close every handler *this module* installed on *logger*.

    Args:
        logger: The logger to prune.

    Returns:
        How many handlers were removed. Handlers belonging to anyone else are left in place -
        pytest's capture handler among them - which is the whole reason this is a targeted
        prune rather than a ``handlers.clear()``.
    """
    managed = [handler for handler in logger.handlers if isinstance(handler, _ManagedHandler)]
    for handler in managed:
        logger.removeHandler(handler)
        # Releases the file descriptor of a file handler. A stream handler's `close` does not
        # touch its stream, so `sys.stderr` is never closed here.
        handler.close()
    return len(managed)


# =============================================================================
# SECTION 7 -- Level and destination resolution.
# =============================================================================


def resolve_level(value: object, default: int = FALLBACK_LEVEL) -> int:
    """Coerce a configured level into the integer :mod:`logging` expects.

    Accepts a standard-library level name in any case (``"info"``, ``"INFO"``, ``" Debug "``),
    a numeric string (``"20"``) and an integer. ``None``, a blank string and anything
    unrecognised fall back to *default* with a warning, because a typo in ``LOG_LEVEL`` must
    not stop an application from starting - and must not pass unnoticed either.

    ``NOTSET`` (``0``) is accepted and means what it means to :mod:`logging`: no threshold of
    the logger's own, so every record is handled.

    Args:
        value: The configured value, straight from the application config or a keyword
            argument.
        default: Level to use when *value* carries no usable level. Defaults to
            :data:`FALLBACK_LEVEL`.

    Returns:
        A level integer suitable for :meth:`logging.Logger.setLevel`.
    """
    if value is None:
        return default
    # `bool` is an `int` subclass, and `True` would silently become level 1. It is not a
    # level, so it is rejected with the same warning as any other unusable value.
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    text = str(value).strip()
    if not text:
        return default
    if text.lstrip("-+").isdigit():
        return int(text)
    named = logging.getLevelNamesMapping().get(text.upper())
    if named is not None:
        return named
    _LOGGER.warning(
        "Unrecognised %s value %r; falling back to %s. Accepted names: %s",
        LOG_LEVEL_CONFIG_KEY,
        text,
        logging.getLevelName(default),
        ", ".join(sorted(logging.getLevelNamesMapping())),
    )
    return default


def _resolve_destination(value: object) -> Path | None:
    """Coerce a configured log destination into a path, or ``None`` when there is none.

    A blank value yields ``None`` rather than the working directory, mirroring
    ``app.config._as_path``: an empty path carries no decision. ``None`` means "no file
    handler", which is how a container-only deployment runs with stream logging alone.

    Args:
        value: The configured value, straight from the application config or a keyword
            argument.

    Returns:
        The destination path, or ``None`` when no usable destination was configured.
    """
    if value is None:
        return None
    if isinstance(value, Path):
        candidate = value
    elif isinstance(value, os.PathLike):
        candidate = Path(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        candidate = Path(text)
    # A NUL byte is checked for explicitly rather than caught: `Path` accepts it happily and
    # only the first filesystem call rejects it, which would otherwise surface as a
    # ValueError from deep inside `Path.resolve()` while an application was starting up.
    if "\x00" in str(candidate):
        _LOGGER.warning(
            "Ignoring unusable %s value %r (embedded NUL byte); file logging is disabled "
            "for this application",
            LOG_FILE_CONFIG_KEY,
            str(candidate),
        )
        return None
    return candidate


def _audit_destination(destination: Path) -> None:
    """Warn about a destination that breaks either of this module's two parity constraints.

    Configuration is never overridden - an operator's explicit choice stands - but both
    hazards are reported, because both are silent and both are costly.

    Args:
        destination: The resolved log destination.
    """
    if destination.suffix != IGNORED_LOG_SUFFIX:
        _LOGGER.warning(
            "Log destination %s does not end in %r, so the preserved ignore pattern "
            "'*.log' [.gitignore:L6] does not cover it: the file is trackable and could be "
            "committed. Honouring the configured value all the same.",
            to_posix(destination),
            IGNORED_LOG_SUFFIX,
        )
    # `resolve()` does not require either path to exist, and normalising both is what makes
    # the comparison correct for a relative destination, an absolute one and a `..` segment
    # alike. It does consult the filesystem, so a refusal is caught: an unresolvable path is
    # not a reason to abandon a diagnostic, and the handler build below reports it anyway.
    try:
        inside_artifact_root = (
            destination.expanduser().resolve().is_relative_to(TARGET_DIR.resolve())
        )
    except (OSError, ValueError) as error:
        _LOGGER.debug(
            "Could not normalise log destination %s for the artifact-root check (%s: %s)",
            to_posix(destination),
            type(error).__name__,
            error,
        )
        return
    if inside_artifact_root:
        _LOGGER.warning(
            "Log destination %s sits inside the artifact root %s, which the port of "
            "`mvn clean` wipes before every run, so the log will be destroyed each time. "
            "Honouring the configured value all the same.",
            to_posix(destination),
            to_posix(TARGET_DIR),
        )


def _build_file_handler(
    destination: Path, formatter: logging.Formatter
) -> logging.FileHandler | None:
    """Build the UTF-8 file handler, or return ``None`` when the filesystem refuses.

    The parent directory is created first, because a handler cannot open a file in a
    directory that does not exist. Only that one directory is created: this module never
    materialises ``target/`` or any part of its layout, which AAP section 0.6 assigns to
    ``app/utils/paths.py``, the ``Makefile`` test target and ``tests/conftest.py``.

    Args:
        destination: Where to append records. Audited by :func:`_audit_destination` first.
        formatter: The formatter to install on the handler.

    Returns:
        The handler, or ``None`` if the parent directory could not be created or the file
        could not be opened - in which case a warning has been logged and the caller
        continues with stream logging alone rather than failing to start.
    """
    _audit_destination(destination)
    if not ensure_parent_directory(destination):
        _LOGGER.warning(
            "Could not create the directory for log destination %s; continuing with stream "
            "logging only",
            to_posix(destination),
        )
        return None
    try:
        # `encoding="utf-8"` is explicit and mandatory (baseline B7). Strict error handling is
        # intentional: an encoding problem must surface rather than silently mangle text such
        # as the U+2013 EN DASH of [README.md:L157, L161]. Append mode preserves history
        # across restarts, and NO rotating handler is used - its `.log.1` backups would escape
        # the preserved '*.log' pattern [.gitignore:L6].
        handler = _ManagedFileHandler(destination, mode="a", encoding="utf-8", delay=False)
    except OSError as error:
        _LOGGER.warning(
            "Could not open log destination %s (%s: %s); continuing with stream logging only",
            to_posix(destination),
            type(error).__name__,
            error,
        )
        return None
    handler.setFormatter(formatter)
    handler.setLevel(logging.NOTSET)
    return handler


# =============================================================================
# SECTION 8 -- The entry point, and the small surface around it.
# =============================================================================


def _settings_of(source: Flask | Mapping[str, object] | None) -> Mapping[str, object]:
    """Return the configuration mapping to read the two logging settings from.

    Args:
        source: A Flask application, a plain configuration mapping (``app.config`` itself is
            one), or ``None``.

    Returns:
        The mapping to read from; empty when *source* is ``None``, which is what makes every
        setting fall back to this module's last-resort defaults.
    """
    if isinstance(source, Flask):
        return source.config
    if source is None:
        return {}
    return source


def configure_logging(
    app: Flask | Mapping[str, object] | None = None,
    *,
    level: str | int | None = None,
    log_file: str | Path | None = None,
    enable_file_logging: bool | None = None,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure structured logging for one application. Call once, inside ``create_app()``.

    Installs one stream handler and - when a destination is configured - one UTF-8 file handler
    on the **root** logger, both rendering :class:`StructuredFormatter`, so application records,
    Flask records and Werkzeug's request log all share one format. Then applies the resolved
    level to the application hierarchy and to ``werkzeug``.

    **Idempotent.** Every call removes and closes only the handlers a previous call installed
    (see :class:`_ManagedHandler`) before installing fresh ones, so handler counts never grow
    and no message is ever written twice - which matters because ``tests/conftest.py`` builds
    one application per test and every ``-n logical`` xdist worker builds its own. Handlers
    owned by anyone else, including pytest's capture handler, are left untouched.

    Explicit arguments outrank the application config, mirroring rung one of the precedence
    chain in ``app/config.py``; the config supplies everything not passed explicitly; and
    :data:`FALLBACK_LEVEL_NAME` applies only when neither does.

    Args:
        app: The Flask application being built, or any configuration mapping (``app.config``
            works), or ``None`` for a process that has no application yet. When a Flask
            application is supplied, its own ``app.logger`` is configured too and Flask's
            ``default_handler`` is removed from it, which is what prevents the double emission
            that would otherwise occur if the factory touched ``app.logger`` first.
        level: Level name or number overriding ``LOG_LEVEL``.
        log_file: Destination overriding ``LOG_FILE``.
        enable_file_logging: Force the file handler on or off. ``None`` - the default - means
            "on when a destination is configured", so a deployment that logs to the container
            stream alone simply supplies no destination.
        stream: Stream for the stream handler. Defaults to ``sys.stderr``, looked up at call
            time so gunicorn's ``capture_output`` redirection is honoured. Tests pass a
            :class:`io.StringIO`.

    Returns:
        The application logger: ``app.logger`` when a Flask application was supplied,
        otherwise the logger named :data:`APPLICATION_LOGGER_NAME`.
    """
    settings = _settings_of(app)
    flask_app = app if isinstance(app, Flask) else None

    formatter = StructuredFormatter()
    root = logging.getLogger()
    replaced = _prune_managed_handlers(root)

    # The stream handler goes on FIRST, before anything can go wrong, so that every diagnostic
    # this function emits below - an unrecognised level, an unusable or ill-advised destination
    # - is itself rendered in the format this module guarantees, instead of escaping through
    # `logging.lastResort`. Handler level NOTSET: the LOGGER decides what is emitted and the
    # handler renders whatever reaches it, because two thresholds would make the effective
    # verbosity a puzzle.
    stream_handler = _ManagedStreamHandler(sys.stderr if stream is None else stream)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.NOTSET)
    root.addHandler(stream_handler)

    resolved_level = resolve_level(
        level if level is not None else settings.get(LOG_LEVEL_CONFIG_KEY)
    )

    configured_destination = log_file if log_file is not None else settings.get(LOG_FILE_CONFIG_KEY)
    if enable_file_logging is False:
        destination: Path | None = None
    else:
        destination = _resolve_destination(configured_destination)
        if enable_file_logging is True and destination is None:
            _LOGGER.warning(
                "File logging was requested but no usable %s was configured; continuing with "
                "stream logging only",
                LOG_FILE_CONFIG_KEY,
            )

    file_handler = None if destination is None else _build_file_handler(destination, formatter)
    destination_note = "disabled"
    if file_handler is not None and destination is not None:
        root.addHandler(file_handler)
        destination_note = to_posix(destination)

    # The root level is only ever LOWERED. Raising it would silence every third-party library
    # below Python's own default of WARNING, which this module must not do. Propagation
    # consults the ORIGINATING logger's effective level and each handler's level, never an
    # ancestor logger's, so `app.*` records still reach these handlers at whatever level the
    # loop below sets.
    root.setLevel(min(resolved_level, logging.WARNING))

    names = [APPLICATION_LOGGER_NAME, WERKZEUG_LOGGER_NAME]
    if flask_app is not None and flask_app.logger.name not in names:
        # A Flask application built with an import name other than this package's - a fixture
        # calling `Flask("fixture")`, say - logs to a logger of that name, which would
        # otherwise inherit only the root threshold.
        names.append(flask_app.logger.name)
    for name in names:
        logger = logging.getLogger(name)
        logger.setLevel(resolved_level)
        # Records have to reach the root handlers installed above; a previous configuration
        # could have detached this logger from the hierarchy.
        logger.propagate = True
        # Flask adds its own StreamHandler to `app.logger` the first time that property is
        # touched while no ancestor has a handler. Removing it is Flask's documented remedy
        # and is a no-op when it was never added, which is the usual case here because the
        # root handler above is installed first.
        logger.removeHandler(_FLASK_DEFAULT_HANDLER)

    # DEBUG, not INFO: this fires once per application, and the factory builds one per test
    # under `-n logical`. Anomalies are reported at WARNING, so nothing that matters is quiet.
    _LOGGER.debug(
        "Structured logging configured: level=%s file=%s redaction=on "
        "(replaced %d managed handler(s))",
        logging.getLevelName(resolved_level),
        destination_note,
        replaced,
    )

    return logging.getLogger(APPLICATION_LOGGER_NAME) if flask_app is None else flask_app.logger


# Documented alias. The application factory may reach for either name; both are this exact
# function, so behaviour cannot diverge between them. `app/utils/paths.py` establishes the
# same idiom for `ensure_target_layout`.
init_logging = configure_logging


def reset_logging() -> None:
    """Undo :func:`configure_logging`, returning the logging system to its pristine state.

    Removes and closes every handler this module installed, clears the levels it set and
    restores the root logger to Python's default ``WARNING``. Intended for a test fixture's
    teardown - so one test's configuration cannot leak into the next - and for a script that
    configured logging and wants to hand the process back untouched. Safe to call when nothing
    was ever configured.
    """
    root = logging.getLogger()
    _prune_managed_handlers(root)
    root.setLevel(logging.WARNING)
    for name in (APPLICATION_LOGGER_NAME, WERKZEUG_LOGGER_NAME):
        logger = logging.getLogger(name)
        _prune_managed_handlers(logger)
        logger.setLevel(logging.NOTSET)


def is_configured() -> bool:
    """Report whether this module has handlers installed on the root logger.

    Returns:
        ``True`` when at least one handler installed by :func:`configure_logging` is still
        attached. Handlers installed by anything else - pytest's capture handler, gunicorn's
        own, a caller's - are not counted, so this answers "did *this module* configure
        logging" rather than "is logging configured at all".
    """
    return any(isinstance(handler, _ManagedHandler) for handler in logging.getLogger().handlers)


def application_logger() -> logging.Logger:
    """Return the root of the application's logger hierarchy.

    Returns:
        The logger named :data:`APPLICATION_LOGGER_NAME`, which is also Flask's ``app.logger``
        for an application the factory built as ``Flask(__name__)`` from ``app/__init__.py``.
        Ordinary modules should keep using ``logging.getLogger(__name__)``; this helper exists
        for a caller that has to address the hierarchy root itself.
    """
    return logging.getLogger(APPLICATION_LOGGER_NAME)


def stage_logger(stage: str) -> logging.Logger:
    """Return the logger that traces one stage of the ported pipeline.

    Args:
        stage: A stage name exactly as ``Jenkins`` spells it - ``'Clone code'``
            ``[Jenkins:L2]``, ``'Run tests'`` ``[Jenkins:L6]`` or ``'Generate report'``
            ``[Jenkins:L14]``. The match is exact, because AAP section 0.8 keeps those three
            names byte-identical to the source.

    Returns:
        The logger named by :data:`STAGE_LOGGER_NAMES`, which is the same logger the
        corresponding service module obtains through ``logging.getLogger(__name__)``.

    Raises:
        ValueError: If *stage* is not one of the three source stage names. The pipeline has
            exactly three stages and no more, so an unknown name is a mistake worth reporting
            rather than a case to absorb.
    """
    name = STAGE_LOGGER_NAMES.get(stage)
    if name is None:
        raise ValueError(
            f"Unknown pipeline stage {stage!r}; the ported pipeline has exactly three: "
            + ", ".join(repr(known) for known in STAGE_LOGGER_NAMES)
        )
    return logging.getLogger(name)
