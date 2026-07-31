"""Stage ``'Clone code'`` -- the Python port of the pipeline's one-line ``git`` step.

This whole module descends from a SINGLE line of Groovy. The source repository's
scripted Jenkins pipeline opened with::

    node {
        stage('Clone code') {
            git 'https://github.com/BalamiRR/Upgenix-QA.git'
        }

which is ``[Jenkins:L1-L4]`` verbatim. Agent Action Plan (AAP) Rule T2 -- "one
source construct, one target module" -- makes that stage this file, and nothing
else in this file exists for any other reason.

Why a bare ``git clone`` would NOT have been a faithful port
-----------------------------------------------------------
AAP section 0.6 says so explicitly, and this paragraph is the design brief for
the entire module:

    "The pipeline's ``git '<url>'`` step ``[Jenkins:L3]`` is not a bare clone: it
    also handles workspace cleanup, credentials and source-control polling.
    ``app/services/clone_service.py`` must therefore handle the already-cloned
    case by fetching and resetting rather than failing, run with an explicit
    timeout, and never interpolate a URL into a shell string -- argument lists
    only, no shell invocation."

Those three obligations are realised as:

Already-cloned is NORMAL, never an error
    Jenkins re-ran that stage against a workspace that usually already held the
    repository, so running this service twice has to succeed twice. A
    destination that already holds a checkout of the requested repository is
    FETCHED and RESET, and :attr:`CloneResult.action` reports ``updated``
    instead of ``cloned`` so the difference stays observable.
An explicit timeout on EVERY subprocess call
    ``app/config.py`` supplies the value (``CLONE_TIMEOUT_SECONDS``, 300
    seconds) and this module never runs ``git`` without one. The budget is a
    whole-stage budget: every call receives what is left of it, so the stage as
    a whole cannot outlive the configured timeout however many ``git``
    invocations it needs. A timeout is reported as a structured
    ``TIMEOUT`` result, never as a raised :class:`subprocess.TimeoutExpired`.
Argument lists only, never a shell
    Every ``git`` invocation is a ``list[str]`` of separate argv tokens executed
    with no shell at all: the shell keyword of :func:`subprocess.run` is never
    enabled, none of the shell-invoking standard-library helpers is used, and no
    command string is ever built by joining or interpolating tokens together. A
    repository URL -- the one value an operator controls -- is therefore never
    parsed by a shell, and ``clone`` additionally receives it after the ``--``
    end-of-options separator. This is AAP section 0.7's baseline B6, which names
    this file by path; the AAP states it twice because it is the
    security-relevant constraint of this package.

Deliberately NOT reproduced
---------------------------
The Groovy ``git`` step also wiped the workspace, bound credentials and polled
source control. AAP section 0.6 prescribes the safe subset -- "fetching and
resetting" -- and AAP section 0.2.2 places Jenkins credentials, agent
configuration and secrets management out of scope, so this module implements
exactly ``clone``, ``fetch``, ``reset`` and ``rev-parse`` and nothing more.
In particular it NEVER runs ``git clean``: a destructive workspace wipe could
delete artifacts sitting beside the checkout, and AAP Rule T6 forbids inventing
behaviour the plan does not prescribe. Nothing here writes to a remote either --
AAP section 0.2.2: the two remote repositories "are referenced as clone targets
and never modified".

Preserved defect D7 -- two contradictory clone URLs
---------------------------------------------------
The source names two different repositories, and both strings survive. The
banner beside :data:`FALLBACK_CLONE_URL` carries the citations, the AAP AMB-7
resolution and the README URL as a commented-out alternative. Nothing in this
module unifies them.

Public API
----------
:data:`STAGE_NAME`
    The stage label, byte-identical to ``[Jenkins:L2]``. ``pipeline_service.py``
    reads it so the orchestrator's result carries the source's own label.
:func:`clone_code`
    The stage itself. Keyword-only, every parameter optional, and it returns a
    :class:`CloneResult` instead of raising for an operational failure.
:class:`CloneResult`
    A frozen, fully typed result. :meth:`CloneResult.as_dict` (and its
    :meth:`CloneResult.to_dict` alias) yields JSON primitives only.
:class:`CloneAction`
    ``cloned`` / ``updated`` / ``failed`` -- the same three values
    ``app/api/schemas.py`` already declares for its own ``CloneAction``.
:class:`CloneErrorCode`
    ``GIT_NOT_FOUND`` / ``TIMEOUT`` / ``GIT_FAILED`` / ``DESTINATION_CONFLICT``.
:class:`CloneServiceError`, :class:`CloneConfigurationError`
    The only exceptions this module raises, and only for a deployment that is
    genuinely misconfigured.
:func:`redact_url`
    The pure credential-redaction helper every log record and every returned URL
    passes through.
:data:`FALLBACK_CLONE_URL`, :data:`FALLBACK_CLONE_BRANCH`,
:data:`FALLBACK_CLONE_TIMEOUT_SECONDS`, :data:`FALLBACK_CLONE_DIRECTORY_NAME`,
:data:`REDACTION_PLACEHOLDER`
    The last-resort values used only when no Flask application configuration is
    reachable, plus the fixed redaction placeholder.

How ``app/api/routes.py`` should bind to this module
---------------------------------------------------
That file is not authored yet, so the mapping is spelled out here rather than
left to be guessed. ``POST /api/v1/clone`` validates a ``CloneRequest`` and
calls::

    result = clone_code(
        url=body.url,                        # CloneRequest.url
        branch=body.branch,                  # CloneRequest.branch
        destination=body.directory,          # CloneRequest.directory
        timeout_seconds=body.timeout_seconds, # CloneRequest.timeout_seconds
    )

then builds its ``CloneResponse`` from the result::

    succeeded        <- result.success
    action           <- result.action                 (already `cloned`/`updated`/`failed`)
    url              <- result.url                    (ALREADY REDACTED)
    branch           <- result.branch
    directory        <- result.destination            (already a POSIX string)
    duration_seconds <- result.duration_seconds
    detail           <- result.message

``result.commit``, ``result.returncode``, ``result.error_code``,
``result.timeout_seconds`` and ``result.stage`` are additional, richer than the
response model needs; they exist for the orchestrator and for logs. A
``success=False`` result is a DOMAIN outcome, not an application error, so it is
answered with ``200`` and a body -- the same rule ``app/errors.py`` states for a
zero-scenario test run. Only :class:`CloneConfigurationError` deserves a 4xx/5xx,
because only it means the deployment itself is wrong.

Design contract
---------------
Configuration by injection, never by importing ``app.config``
    Every parameter may be passed explicitly. Anything left unset is read off
    ``flask.current_app.config`` when an application context is active, and
    falls back to a cited module constant otherwise. ``app/config.py`` remains
    the single owner of the five-rung precedence chain
    (``explicit -> environment -> .env -> configuration.properties -> default``);
    this module re-implements no rung of it and never loads ``.env``. The result
    is a module that unit-tests with no Flask application at all.
``configuration.properties`` is normally ABSENT
    It is git-ignored at ``[.gitignore:L3]``, so a fresh checkout never has it.
    Every setting therefore has a documented default and nothing here requires
    the file to exist.
Structured results, not exceptions, for operational outcomes
    ``git`` missing, a timeout, a non-zero ``git`` exit and an occupied
    destination are all reported as ``success=False`` results carrying a machine
    readable :class:`CloneErrorCode`. Only genuine misconfiguration raises.
Credentials are redacted everywhere
    The default URL ``[Jenkins:L3]`` is public and safe to log, but an operator
    may configure one with embedded credentials. Every log record, every
    returned URL and every captured ``git`` stream passes through redaction
    first, so a password cannot reach a log file, a response body or a result
    object. Baseline B5: never hard-code a secret and never log one.
Layering
    AAP Rule T7: ``api -> services -> reporting -> utils``. This module imports
    the standard library, ``flask`` (for ``current_app`` only) and
    ``app.utils.paths``. It never imports ``app.api`` or ``app.web`` -- that is
    the caller's direction -- and never anything under ``tests/`` or
    ``scripts/``. It imports no test-only distribution either: the deployed
    container installs ``requirements.txt`` alone, and ``import wsgi`` has to
    keep working there.
No platform dispatch
    ``app/utils/platform_exec.py`` is deliberately NOT imported. The pipeline's
    ``isUnix()`` branch ``[Jenkins:L7-L11]`` wraps only ``stage('Run tests')``;
    the clone stage ``[Jenkins:L2-L4]`` has no branch at all, and ``git`` is the
    same binary with the same argv on every platform. There is nothing here to
    dispatch on, so no dependency on that helper is created.
Never touches ``target/``
    The artifact root is created by ``app/utils/paths.py``, the ``Makefile``
    test target and ``tests/conftest.py`` alone (AAP section 0.6), and it must
    never be renamed because the publisher glob ``fileIncludePattern:
    '**/*.json'`` ``[Jenkins:L15]`` depends on the name. This module only
    consumes ``app.utils.paths.target_root`` to REFUSE a destination that would
    land inside it -- the very next stage wipes that directory.
Import is free of side effects
    Importing this module starts no process, opens no socket and creates no
    directory. In particular :func:`tempfile.gettempdir` is called at call time,
    never at import time, because it probes candidate directories for
    writability.
"""

import logging
import math
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final
from urllib.parse import unquote, urlsplit, urlunsplit

from flask import current_app, has_app_context

from app.utils.paths import StrPath, target_root, to_posix

__all__ = [
    "CloneAction",
    "CloneConfigurationError",
    "CloneErrorCode",
    "CloneResult",
    "CloneServiceError",
    "FALLBACK_CLONE_BRANCH",
    "FALLBACK_CLONE_DIRECTORY_NAME",
    "FALLBACK_CLONE_TIMEOUT_SECONDS",
    "FALLBACK_CLONE_URL",
    "REDACTION_PLACEHOLDER",
    "STAGE_NAME",
    "clone_code",
    "redact_url",
]

# A module logger and nothing more. Handlers, levels and formatters are owned
# exclusively by `app/logging_config.py` (baseline B9): a service module must
# never reconfigure logging on behalf of the process that imports it, so nothing
# in this file installs or removes a handler, changes a level, applies a logging
# configuration or writes to standard output. Every message below is a lazily
# formatted, structured log record on this one logger.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


# =============================================================================
# The stage label.
#
# [Jenkins:L2] is `    stage('Clone code') {`, so the label is exactly
# `Clone code`: capital C, lower-case c, one space, no punctuation. AAP section
# 0.8 is binding here -- "All three stage names, the platform dispatch, and the
# publisher invocation [Jenkins:L15] stay byte-identical. Only the two command
# strings change." `pipeline_service.py` reads this constant so the
# orchestrator's returned structure shows the source's own label rather than a
# paraphrase of it, and it is repeated in every log record and in every
# CloneResult this module returns.
# =============================================================================

STAGE_NAME: Final[str] = "Clone code"
"""The pipeline stage label, byte-identical to ``[Jenkins:L2]``."""


# =============================================================================
# PRESERVED DEFECT D7 -- TWO CONTRADICTORY REPOSITORY URLS.
#
# The pipeline clones
#
#     git 'https://github.com/BalamiRR/Upgenix-QA.git'          [Jenkins:L3]
#
# while the README's "Framework set up" section instructs cloning
#
#     git clone https://github.com/BalamiRR/Testinium-QA.git    [README.md:L59]
#
# AAP AMB-7 resolves it verbatim: "The pipeline URL becomes the configured
# runtime default; the README URL is retained in documentation as the human clone
# instruction; both are settable through one configuration key. The discrepancy
# is documented, not silently unified... Executable configuration outranks prose
# when the two disagree, and the Jira key prefix `UPGN` [README.md:L115] agrees
# with the pipeline URL."
#
# So the PIPELINE URL is the default below, and the README's URL is retained
# here as a commented-out ALTERNATIVE so no information is lost:
#
#     # FALLBACK_CLONE_URL: Final[str] = "https://github.com/BalamiRR/Testinium-QA.git"
#
# Do NOT unify the two. `docs/migration-parity.md` is the authoritative register
# of defects D1 through D9 (baseline B12), and `app/config.py` additionally keeps
# the README URL machine-readable as `DOCUMENTED_CLONE_URL` /
# `Config.CLONE_URL_DOCUMENTED` so the configuration endpoint can report both.
#
# AUTHORITY. `app/config.py` owns the runtime default: its `DEFAULT_CLONE_URL`
# feeds `Config.CLONE_URL` through the five-rung precedence chain, and this
# module reads that resolved value off `current_app.config`. The constant below
# is a CITED MIRROR of the same source literal (AAP Rule T1 -- "Every literal is
# carried into the target as an explicit constant or default with its source
# cited"), used ONLY when there is no Flask application configuration to read,
# which is the direct programmatic and unit-test case. It is deliberately not a
# second authority: change the value through configuration, never here.
# =============================================================================

FALLBACK_CLONE_URL: Final[str] = "https://github.com/BalamiRR/Upgenix-QA.git"
"""Repository cloned when no configuration is reachable. Source: ``[Jenkins:L3]``.

A cited mirror of ``app/config.py``'s ``DEFAULT_CLONE_URL``, consulted only outside a
Flask application context. Preserved defect **D7** -- see the banner above; the README's
``Testinium-QA`` URL ``[README.md:L59]`` is retained there, commented out.
"""

FALLBACK_CLONE_BRANCH: Final[str] = "main"
"""Branch checked out when no configuration is reachable.

A cited mirror of ``app/config.py``'s ``DEFAULT_CLONE_BRANCH``. The Groovy ``git '<url>'``
step ``[Jenkins:L3]`` passed no explicit ref and therefore took the remote's default
branch; ``main`` is that branch for both repositories the source names, and the README
links ``.../archive/main.zip`` ``[README.md:L63]``. Set the branch to an empty value to
restore the "whatever the remote's HEAD is" behaviour exactly -- see :func:`clone_code`.
"""

FALLBACK_CLONE_TIMEOUT_SECONDS: Final[float] = 300.0
"""Whole-stage ``git`` timeout used when no configuration is reachable.

A cited mirror of ``app/config.py``'s ``DEFAULT_CLONE_TIMEOUT_SECONDS`` (``300``), whose
own documentation states that this module "always runs ``git`` under an explicit timeout:
a clone that blocked for ever would hang the whole ported pipeline". ADDITIVE: the Groovy
step had no timeout at all, because Jenkins bounded the build instead.
"""

FALLBACK_CLONE_DIRECTORY_NAME: Final[str] = "testinium-qa-clone"
"""Name of the checkout directory created under the system temporary directory.

ADDITIVE, and deliberately NOT a mirror of ``app/config.py``'s ``CLONE_DIR``
(``workspace``, relative to the repository root). Jenkins cloned into its *workspace*,
which a plain Python process has no analogue for, so the port has to choose somewhere.
When an application context is active the CONFIGURED value wins and ``app/config.py``
stays authoritative. This module's own last-resort default instead sits under
:func:`tempfile.gettempdir`, for two concrete reasons: it is outside this repository, so
a bare programmatic call can never write into the tree being ported; and it needs no new
``.gitignore`` entry, whereas ``workspace/`` is not ignored and a default run would
otherwise leave the working tree dirty. Operators may override it freely -- a deployment
that keeps the checkout inside the repository should ignore that directory itself.
"""

REDACTION_PLACEHOLDER: Final[str] = "REDACTED"
"""Fixed text substituted for anything credential-shaped before it is logged or returned.

Baseline B5 -- never hard-code a secret and never log one. Deliberately a value that
cannot match any provider's credential pattern, so a redacted record can never be
mistaken for a real one.
"""


# =============================================================================
# Private constants.
#
# The four git subcommands below are the ONLY ones this module ever runs. AAP
# section 0.6 prescribes "fetching and resetting" for the already-cloned case and
# AAP section 0.2.2 forbids modifying a remote, so there is deliberately no
# `push`, no `clean`, no `remote` and no `config` subcommand anywhere in this
# file. The origin URL of an existing checkout is read from `.git/config` as text
# for exactly that reason.
# =============================================================================

_GIT_EXECUTABLE: Final[str] = "git"
_SUBCOMMAND_CLONE: Final[str] = "clone"
_SUBCOMMAND_FETCH: Final[str] = "fetch"
_SUBCOMMAND_RESET: Final[str] = "reset"
_SUBCOMMAND_REV_PARSE: Final[str] = "rev-parse"

# `--` is git's documented end-of-options separator for clone
# (`git clone [<options>] [--] <repo> [<dir>]`), so a URL can never be
# reinterpreted as an option even before the leading-dash rejection in
# `_validated_url`. git does not document `--` for `fetch`, `reset` or
# `rev-parse`, and those three receive only fixed literals plus a ref that
# `_validated_ref` has already constrained to a strict character allowlist.
_END_OF_OPTIONS: Final[str] = "--"

_ORIGIN_REMOTE: Final[str] = "origin"
_FETCH_HEAD_REF: Final[str] = "FETCH_HEAD"
_HARD_RESET_FLAG: Final[str] = "--hard"
_BRANCH_FLAG: Final[str] = "--branch"

# `HEAD` serves two distinct purposes, both of them fixed literals rather than
# anything an operator supplies: `git fetch origin HEAD` asks the REMOTE for
# whatever its default branch is -- which is exactly what the ref-less Groovy step
# `[Jenkins:L3]` took -- and `git rev-parse HEAD` reads the LOCAL commit the
# checkout ended up at.
_HEAD_REF: Final[str] = "HEAD"

# Administrative directory of a checkout, the file that replaces it in a linked
# worktree or a submodule, and the name of the config file inside it. Read as
# TEXT to discover an existing checkout's `origin` URL, because `git remote` and
# `git config` are outside the four subcommands this module is allowed to run.
_GIT_DIRECTORY_NAME: Final[str] = ".git"
_GIT_CONFIG_NAME: Final[str] = "config"
_GIT_FILE_PREFIX: Final[str] = "gitdir:"
_REMOTE_SECTION_NAME: Final[str] = "remote"
_URL_KEY_NAME: Final[str] = "url"

# The conventional suffix of a repository URL -- spelled the same as
# `_GIT_DIRECTORY_NAME` but meaning something entirely different, which is why it
# has its own name. Ignored when two remote URLs are compared, so
# `https://host/x.git` and `https://host/x` are recognised as the same remote. It
# is deliberately NOT ignored for local paths, where `/srv/x.git` and `/srv/x` are
# two different directories.
_GIT_URL_SUFFIX: Final[str] = ".git"

# Transports git accepts that this module is willing to hand it. Anything else --
# and in particular the `ext::` transport, which makes git execute an arbitrary
# command supplied inside the URL, plus every other `<helper>::` remote-helper
# form -- is refused before a subprocess is created. `urlsplit` lower-cases the
# scheme it reports, so `EXT::` is caught as surely as `ext::`.
_ALLOWED_URL_SCHEMES: Final[frozenset[str]] = frozenset({"file", "git", "http", "https", "ssh"})

# The remote-helper separator. An unschemed candidate containing it is refused:
# `urlsplit` reports no scheme for shapes such as `./ext::sh -c ...`, so the
# scheme allowlist alone would let them through. A scheme-bearing URL is
# unaffected, which keeps IPv6 literals such as `https://[::1]/x.git` valid.
_REMOTE_HELPER_SEPARATOR: Final[str] = "::"

# Characters a ref may contain. Conservative on purpose: it excludes whitespace,
# shell metacharacters, `~`, `^`, `:`, `?`, `*`, `[` and every control character,
# all of which git itself rejects in a ref name, and it excludes a leading `-` so
# a ref can never be read as an option by `fetch`.
_ALLOWED_REF_CHARACTERS: Final[frozenset[str]] = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-"
)

# Environment overrides applied to every `git` invocation. An interactive
# credential prompt would otherwise sit there consuming the whole timeout budget
# before failing, which is precisely what AAP section 0.6's "explicit timeout"
# requirement exists to prevent. git treats an EMPTY askpass value as unset, so
# the two askpass helpers are neutralised by emptying them and the terminal
# prompt is then disabled outright.
_GIT_TERMINAL_PROMPT_DISABLED: Final[Mapping[str, str]] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
}

# Upper bound on the human-readable message built from captured `git` output. A
# message is a summary, not a transcript: the full streams stay in the debug log.
_MAX_MESSAGE_LENGTH: Final[int] = 500

# Floor handed to a subprocess when the whole-stage budget is nearly exhausted.
# `subprocess.run(timeout=0)` would raise immediately and report a timeout for a
# call that never had a chance to run, so the last sliver of budget is rounded up
# to something a process can at least start within.
_MINIMUM_CALL_TIMEOUT_SECONDS: Final[float] = 0.05

# Keys read off `current_app.config`. These are `app/config.py`'s own attribute
# names, spelled exactly as `Config` declares them -- AAP section 0.7 forbids
# inventing a parallel setting name, and `Config.CLONE_*` is the single
# authority. `CLONE_URL_DOCUMENTED` is deliberately absent: this module clones
# one repository, and reporting the OTHER URL that defect D7 preserves is the
# configuration endpoint's job, not this stage's.
_CONFIG_KEY_URL: Final[str] = "CLONE_URL"
_CONFIG_KEY_BRANCH: Final[str] = "CLONE_BRANCH"
_CONFIG_KEY_DIRECTORY: Final[str] = "CLONE_DIR"
_CONFIG_KEY_TIMEOUT: Final[str] = "CLONE_TIMEOUT_SECONDS"

# The repository root, derived from this module's own location and never from the
# process working directory -- `app/services/clone_service.py`, so parents[0] is
# `app/services`, parents[1] is `app` and parents[2] is the root. This is the
# convention `app/config.py` (parents[1]) and `app/utils/properties.py`
# (parents[2]) already follow, and it means the same paths resolve whether the
# application was started by `flask run`, by gunicorn from a container WORKDIR or
# by pytest from a subdirectory. It is a filesystem fact rather than a
# configuration value, so deriving it here duplicates no setting.
_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

# The ephemeral artifact root, obtained from the ONE module that owns the
# `target/` name so no path literal is restated here. Used only to REFUSE a
# destination: stage `'Run tests'` wipes this directory immediately after this
# stage runs, so a checkout placed inside it would be deleted by the very next
# step of the pipeline. `target_root` is pure path composition and touches no
# filesystem, so evaluating it at import time keeps this module import-pure.
_TARGET_ROOT: Final[Path] = target_root(_PROJECT_ROOT)


# =============================================================================
# Vocabularies.
#
# `StrEnum` members so a value serialises straight into a JSON payload with no
# encoder, exactly as `app/api/schemas.py` expects of everything it receives from
# this layer.
# =============================================================================


class CloneAction(StrEnum):
    """What the clone stage actually did.

    Three values rather than a boolean, because the source stage's behaviour cannot be
    described by one. The Groovy ``git '<url>'`` step ``[Jenkins:L2-L4]`` was not a bare
    clone: it re-used an existing workspace, so running it twice was normal and never an
    error. The port therefore has to report *which* of those happened.

    The three values are identical to those of ``app/api/schemas.py``'s own ``CloneAction``,
    so the API layer can coerce one into the other with no translation table. They are
    restated rather than imported because AAP Rule T7 fixes the dependency direction as
    ``api -> services``: importing back from ``app.api`` would close a cycle.
    """

    CLONED = "cloned"
    """The destination was absent or empty and a fresh clone was made."""

    UPDATED = "updated"
    """The destination already held the repository, which was fetched and reset."""

    FAILED = "failed"
    """Neither could be completed. Always paired with ``success=False`` and an error code."""


class CloneErrorCode(StrEnum):
    """Why the stage did not complete, in machine-readable form.

    Every member is produced by this module, and every one is an OPERATIONAL outcome
    reported as a :class:`CloneResult` with ``success=False`` -- never as a raised
    exception, so a caller can answer ``200`` with a body instead of a ``500``. Genuine
    misconfiguration is the one thing that raises, as :class:`CloneConfigurationError`.
    """

    GIT_NOT_FOUND = "GIT_NOT_FOUND"
    """The ``git`` executable is not on ``PATH``.

    The deployment image installs it precisely so this stage can run, so this code means
    the runtime environment is incomplete rather than that the request was wrong.
    """

    TIMEOUT = "TIMEOUT"
    """The whole-stage timeout expired while ``git`` was running.

    The port of AAP section 0.6's "run with an explicit timeout": reported as a result, so
    :class:`subprocess.TimeoutExpired` never escapes this module.
    """

    GIT_FAILED = "GIT_FAILED"
    """``git`` ran and exited non-zero, or could not be executed at all.

    :attr:`CloneResult.returncode` carries the exit status when there was one, and
    :attr:`CloneResult.message` a redacted summary of what ``git`` said.
    """

    DESTINATION_CONFLICT = "DESTINATION_CONFLICT"
    """The destination cannot be used, and nothing was clobbered to force it.

    Raised as a result -- not an exception -- when the destination already holds something
    that is not a checkout of the requested repository, when its ``origin`` points
    somewhere else, or when the filesystem refuses the path. Overwriting it is never
    attempted: AAP section 0.6 prescribes fetch-and-reset for an existing checkout and
    says nothing whatsoever about deleting a stranger's files.
    """


# =============================================================================
# Exceptions.
#
# Declared HERE, in the module that raises them, rather than in a new
# `app/services/exceptions.py`: AAP section 0.3.1's tree shows exactly five files
# in a flat `app/services/` package, and AAP section 0.8 is binding -- "No feature
# may be dropped, and none may be added." `pipeline_service.py` imports these two
# names upward, which is the permitted leaf -> orchestrator direction.
# =============================================================================


class CloneServiceError(Exception):
    """Base class for every error stage ``'Clone code'`` raises.

    Deliberately narrow. This module reports operational failures -- a missing ``git``, a
    timeout, a non-zero exit, an occupied destination -- as :class:`CloneResult` values
    with ``success=False``, because none of those is an application fault. What remains,
    and therefore what this hierarchy covers, is a deployment whose configuration cannot
    be acted on at all.
    """


class CloneConfigurationError(CloneServiceError, ValueError):
    """The resolved configuration for the stage is unusable.

    Raised BEFORE any subprocess is created, so nothing has run and nothing has been
    written when it surfaces. The four causes are:

    * an empty repository URL;
    * a URL git would misread -- one starting with ``-``, one carrying a control
      character, one naming a transport outside the allowlist, or a ``<helper>::`` remote
      helper form such as ``ext::``, which lets a URL execute an arbitrary command;
    * a ref that is not a plain branch or tag name;
    * an unsafe destination -- the repository root itself, a directory containing it, or
      anywhere inside the ephemeral ``target/`` tree that the very next pipeline stage
      wipes.

    Also a :class:`ValueError`, so a caller that already funnels bad input through
    ``except ValueError`` keeps working; ``app/errors.py`` may map it to a 4xx because it
    describes the request or the deployment, never a server fault.
    """


# =============================================================================
# The result.
# =============================================================================


@dataclass(frozen=True, slots=True)
class CloneResult:
    """Immutable outcome of one run of stage ``'Clone code'``.

    Frozen and slotted, so a result handed to a log record, a template or a response
    builder cannot be edited after the fact and two callers can safely share one. Every
    field is a JSON primitive, a :class:`~enum.StrEnum` member or ``None``: no
    :class:`~pathlib.Path`, no :class:`~datetime.datetime` and no exception is ever stored,
    which is what lets :meth:`as_dict` be serialised as it stands.

    Example:
        >>> outcome = CloneResult(
        ...     success=True,
        ...     action=CloneAction.UPDATED,
        ...     url="https://github.com/BalamiRR/Upgenix-QA.git",
        ...     destination="/tmp/testinium-qa-clone",
        ...     branch="main",
        ...     commit="0" * 40,
        ...     duration_seconds=1.5,
        ...     timeout_seconds=300.0,
        ...     returncode=0,
        ...     error_code=None,
        ...     message="Existing checkout fetched and reset.",
        ... )
        >>> outcome.stage
        'Clone code'
        >>> outcome.as_dict()["action"]
        'updated'
    """

    success: bool
    """Whether the stage completed. ``False`` is a domain outcome, not an application error."""

    action: CloneAction
    """Which path was taken: a fresh clone, an update of an existing checkout, or neither."""

    url: str
    """The repository that was used, ALREADY REDACTED.

    Any userinfo an operator embedded has been replaced by :data:`REDACTION_PLACEHOLDER`,
    so this value is safe to log, to store and to serve. The unredacted form is never
    stored anywhere and nothing re-derives it.
    """

    destination: str
    """Absolute destination directory as a POSIX string, on every platform.

    A string rather than a :class:`~pathlib.Path` so the result is directly
    :func:`json.dumps`-able, rendered by ``app/utils/paths.to_posix`` so one spelling is
    used everywhere.
    """

    branch: str | None
    """The ref that was requested, or ``None`` when the remote's default branch was taken.

    ``None`` reproduces the Groovy step exactly: ``git '<url>'`` ``[Jenkins:L3]`` named no
    ref and therefore took whatever the remote's ``HEAD`` pointed at.
    """

    commit: str | None
    """Commit the destination ended up at, from ``git rev-parse HEAD``.

    ``None`` when it could not be determined -- an empty repository has no ``HEAD`` -- which
    is not a stage failure: the checkout itself still succeeded.
    """

    duration_seconds: float
    """Wall-clock duration of the stage, measured on a monotonic clock."""

    timeout_seconds: float
    """The whole-stage timeout that was in force, echoed back for auditability."""

    returncode: int | None
    """Exit status of the last ``git`` invocation, or ``None`` when none completed."""

    error_code: CloneErrorCode | None
    """Machine-readable failure reason, or ``None`` on success."""

    message: str
    """One-line, human-readable explanation. Always redacted, never a secret.

    Bounded in length and stripped of line breaks, because it is a summary destined for a
    response body and a log line rather than a transcript; the full ``git`` streams stay in
    the debug log. This module interpolates no filesystem path into it -- the destination
    has its own field -- although text quoted from ``git`` may name paths ``git`` itself
    chose to print.
    """

    @property
    def stage(self) -> str:
        """Return the pipeline stage label, byte-identical to ``[Jenkins:L2]``.

        A property rather than a stored field so the label can never be constructed with a
        different value: there is exactly one spelling of it, :data:`STAGE_NAME`.
        """
        return STAGE_NAME

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-ready view of this result.

        Every value is a primitive or ``None``: the two enumerations are rendered as their
        string values, so the mapping can be handed to :func:`json.dumps`, to Flask's
        ``jsonify`` or to a pydantic model with no encoder and no post-processing. A fresh
        dictionary is built on each call, so a caller can never reach back into the frozen
        result through it.

        Returns:
            The twelve public attributes of this result, keyed by their own names, with
            :attr:`stage` included so a payload carries the source's stage label.
        """
        return {
            "stage": self.stage,
            "success": self.success,
            "action": self.action.value,
            "url": self.url,
            "destination": self.destination,
            "branch": self.branch,
            "commit": self.commit,
            "duration_seconds": self.duration_seconds,
            "timeout_seconds": self.timeout_seconds,
            "returncode": self.returncode,
            "error_code": None if self.error_code is None else self.error_code.value,
            "message": self.message,
        }

    def to_dict(self) -> dict[str, object]:
        """Return :meth:`as_dict`. A named alias, so no caller has to guess the spelling.

        ``as_dict`` is this codebase's convention -- ``app/reporting/*`` and
        ``app/config.py`` all use it -- while ``to_dict`` is the more common name in the
        wider Python ecosystem. ``app/api/routes.py`` is not authored yet, so both are
        provided and both are supported; they return equal, independent dictionaries.

        Returns:
            Exactly what :meth:`as_dict` returns.
        """
        return self.as_dict()


# =============================================================================
# Credential redaction.
#
# The default clone URL [Jenkins:L3] is a public GitHub address and is perfectly
# safe to log, but an operator may configure one carrying embedded credentials.
# `app/logging_config.py` assigns the redaction obligation to this module, so
# every log record, every URL stored in a CloneResult and every captured `git`
# stream passes through the helpers below FIRST. Baseline B5: never hard-code a
# secret and never log one.
# =============================================================================


def _userinfo(value: str) -> str:
    """Return the userinfo component of *value*, or ``""`` when it carries none.

    Handles both URL shapes git accepts: a scheme-bearing URL, whose userinfo lives in the
    authority ahead of the last ``@``, and git's scp-like ``user@host:path`` form, which
    :func:`~urllib.parse.urlsplit` reports as having no scheme at all. An ``@`` appearing
    after the first ``/`` belongs to a path segment, not to an authority, so it is ignored.

    Args:
        value: A repository URL, a local path, or any other command-line token.

    Returns:
        The raw userinfo -- which may be ``user`` or ``user:password`` -- or ``""``.
    """
    if not value:
        return ""
    try:
        split = urlsplit(value)
    except ValueError:
        return ""
    if split.scheme and split.netloc:
        marker = split.netloc.rfind("@")
        return "" if marker == -1 else split.netloc[:marker]
    marker = value.find("@")
    if marker == -1:
        return ""
    slash = value.find("/")
    if slash != -1 and slash < marker:
        return ""
    return value[:marker]


def redact_url(value: str) -> str:
    """Return *value* with any embedded credentials replaced by a fixed placeholder.

    Pure and total: it starts no process, reads no file and never raises. A value carrying
    no userinfo is returned UNCHANGED -- byte for byte, with no re-casing, no added trailing
    slash and no other normalisation -- because AAP Rule T1 forbids normalising a preserved
    configuration value. Only when there is something to hide is the value rewritten.

    Safe to apply to any command-line token, not just to URLs, which is what lets the
    argument list of every ``git`` invocation be logged verbatim-but-safe.

    Args:
        value: A repository URL, or any token that might contain one.

    Returns:
        *value* unchanged when it carries no userinfo; the same URL with its userinfo
        replaced by :data:`REDACTION_PLACEHOLDER` when it does; or
        :data:`REDACTION_PLACEHOLDER` alone when the value cannot be parsed at all, because
        an unparseable value must fail closed rather than be logged on the chance that it
        holds no secret.

    Example:
        >>> redact_url("https://github.com/BalamiRR/Upgenix-QA.git")
        'https://github.com/BalamiRR/Upgenix-QA.git'

        A URL that carries a userinfo component -- whatever precedes the ``@`` in its
        authority -- comes back with that entire component replaced by the placeholder,
        so what reaches the log reads ``https://REDACTED@example.com/x.git``: the host
        and path stay diagnosable while the credential does not survive. No
        credential-shaped literal is spelled out here, so this file cannot itself trip a
        secret scanner.
    """
    if not value:
        return value
    try:
        split = urlsplit(value)
    except ValueError:
        return REDACTION_PLACEHOLDER
    userinfo = _userinfo(value)
    if not userinfo:
        return value
    if split.scheme and split.netloc:
        host = split.netloc[len(userinfo) + 1 :]
        return urlunsplit(split._replace(netloc=f"{REDACTION_PLACEHOLDER}@{host}"))
    return f"{REDACTION_PLACEHOLDER}@{value[len(userinfo) + 1 :]}"


def _redactions(url: str) -> tuple[tuple[str, str], ...]:
    """Return the ``(needle, replacement)`` pairs that scrub *url*'s secrets from text.

    Ordered LONGEST needle first, so replacing the bare password cannot pre-empt replacing
    the whole URL and leave a half-scrubbed fragment behind. Both the raw and the
    percent-decoded forms of the credential are included, because git may echo either.

    Args:
        url: The repository URL that was handed to ``git``.

    Returns:
        An empty tuple when *url* carries no credentials, so the common case costs nothing.
    """
    userinfo = _userinfo(url)
    if not userinfo:
        return ()
    pairs: dict[str, str] = {url: redact_url(url), userinfo: REDACTION_PLACEHOLDER}
    _, _, password = userinfo.partition(":")
    for candidate in (password, unquote(password), unquote(userinfo)):
        if candidate:
            pairs.setdefault(candidate, REDACTION_PLACEHOLDER)
    return tuple(sorted(pairs.items(), key=lambda pair: len(pair[0]), reverse=True))


def _redact_text(text: str, *, url: str) -> str:
    """Return *text* with every credential fragment of *url* replaced.

    Applied to captured ``git`` output before it reaches a log record or a
    :attr:`CloneResult.message`, because git happily echoes the URL it was given back in an
    error message -- credentials and all.

    Args:
        text: Captured output, or any other text about to be reported.
        url: The URL whose credentials must not survive into *text*.

    Returns:
        The scrubbed text, or *text* itself when *url* carries no credentials.
    """
    if not text:
        return text
    scrubbed = text
    for needle, replacement in _redactions(url):
        scrubbed = scrubbed.replace(needle, replacement)
    return scrubbed


def _summarise(text: str) -> str:
    """Collapse *text* to a single bounded line fit for a message field.

    Runs of whitespace -- including the newlines git writes -- become single spaces, and
    anything longer than :data:`_MAX_MESSAGE_LENGTH` is truncated with a plain ASCII
    ellipsis. The untruncated streams are still available in the debug log.

    Args:
        text: Text to summarise. Assumed already redacted by :func:`_redact_text`.

    Returns:
        A single-line summary, possibly empty.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= _MAX_MESSAGE_LENGTH:
        return collapsed
    return collapsed[: _MAX_MESSAGE_LENGTH - 3] + "..."


def _redacted_arguments(arguments: Sequence[str]) -> list[str]:
    """Return *arguments* with every token passed through :func:`redact_url`.

    Used only for logging. The list actually handed to :func:`subprocess.run` is always the
    unredacted one, because git needs the real URL; what reaches the log never does.

    Args:
        arguments: The argv tokens about to be executed.

    Returns:
        A fresh list, safe to log.
    """
    return [redact_url(token) for token in arguments]


# =============================================================================
# Validation.
#
# Everything here runs BEFORE a subprocess exists, so a rejected value has caused
# nothing to happen. This is the enforcement point for baseline B6: the URL an
# operator controls is never handed to a shell, and it is not even handed to git
# until it has been proved to be a transport git will treat as data.
# =============================================================================


def _has_control_characters(value: str) -> bool:
    """Return whether *value* contains a C0 control character or DEL.

    A newline or a NUL smuggled into a URL or a ref is how a single argument becomes two,
    or how a ref name corrupts a git reference file, so such values are refused outright.

    Args:
        value: The text to inspect.

    Returns:
        ``True`` when at least one character is a control character.
    """
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _validated_url(candidate: str) -> str:
    """Return *candidate* as a repository URL this module is willing to give git.

    Surrounding whitespace is stripped -- a value read from ``.env`` or from
    ``configuration.properties`` can arrive with a trailing newline -- and nothing else
    about the value is altered, per AAP Rule T1.

    Args:
        candidate: The URL resolved from the argument, the configuration or the fallback.

    Returns:
        The whitespace-stripped URL.

    Raises:
        CloneConfigurationError: If the URL is empty; starts with ``-``, which git would
            read as an option; carries a control character; cannot be parsed; names a
            transport outside :data:`_ALLOWED_URL_SCHEMES`; or is an unschemed value
            containing ``::``, the remote-helper separator that makes ``ext::<command>``
            execute arbitrary commands. Every message reports the REDACTED URL, so a
            rejection cannot leak the credentials it was rejecting.
    """
    value = candidate.strip()
    if not value:
        raise CloneConfigurationError(
            f"stage {STAGE_NAME!r} has no repository URL to clone. Set {_CONFIG_KEY_URL} "
            f"(or the {_CONFIG_KEY_URL.lower().replace('_', '.')} property); the source "
            f"default is the pipeline's own URL from [Jenkins:L3]."
        )
    safe = redact_url(value)
    if value.startswith("-"):
        raise CloneConfigurationError(
            f"stage {STAGE_NAME!r} refuses the repository URL {safe!r}: a value starting "
            "with '-' would be read by git as a command-line option."
        )
    if _has_control_characters(value):
        raise CloneConfigurationError(
            f"stage {STAGE_NAME!r} refuses the repository URL {safe!r}: it contains a "
            "control character."
        )
    try:
        split = urlsplit(value)
    except ValueError as error:
        raise CloneConfigurationError(
            f"stage {STAGE_NAME!r} could not parse the repository URL {safe!r}: {error}"
        ) from error
    if split.scheme:
        if split.scheme not in _ALLOWED_URL_SCHEMES:
            allowed = ", ".join(sorted(_ALLOWED_URL_SCHEMES))
            raise CloneConfigurationError(
                f"stage {STAGE_NAME!r} refuses the repository URL {safe!r}: the transport "
                f"{split.scheme!r} is not one of {allowed}. In particular git's 'ext::' "
                "transport is rejected deliberately, because it lets a URL name an "
                "arbitrary command for git to execute."
            )
    elif _REMOTE_HELPER_SEPARATOR in value:
        raise CloneConfigurationError(
            f"stage {STAGE_NAME!r} refuses the repository URL {safe!r}: an unschemed value "
            f"containing {_REMOTE_HELPER_SEPARATOR!r} selects a git remote helper, which "
            "can execute an arbitrary command."
        )
    return value


def _validated_ref(candidate: str) -> str:
    """Return *candidate* as a branch or tag name this module is willing to give git.

    Deliberately far stricter than git's own ``check-ref-format``: only a plain branch or
    tag name is accepted. That is all the ported stage needs -- the source named no ref at
    all ``[Jenkins:L3]`` and ``app/config.py`` defaults it to ``main`` -- and it is what
    lets ``fetch`` be invoked safely without an end-of-options separator git does not
    document for it.

    Args:
        candidate: A non-empty, already whitespace-stripped ref name.

    Returns:
        *candidate* unchanged.

    Raises:
        CloneConfigurationError: If the ref starts with ``-`` or ``.``, ends with ``/`` or
            ``.lock``, contains ``..`` or ``//``, or uses any character outside
            :data:`_ALLOWED_REF_CHARACTERS`.
    """
    if candidate.startswith("-"):
        raise CloneConfigurationError(
            f"stage {STAGE_NAME!r} refuses the ref {candidate!r}: a value starting with "
            "'-' would be read by git as a command-line option."
        )
    if not set(candidate) <= _ALLOWED_REF_CHARACTERS:
        raise CloneConfigurationError(
            f"stage {STAGE_NAME!r} refuses the ref {candidate!r}: only letters, digits, "
            "'.', '_', '/' and '-' are accepted in a branch or tag name."
        )
    if (
        candidate.startswith((".", "/"))
        or candidate.endswith(("/", ".lock", "."))
        or ".." in candidate
        or "//" in candidate
    ):
        raise CloneConfigurationError(
            f"stage {STAGE_NAME!r} refuses the ref {candidate!r}: it is not a well-formed "
            "branch or tag name."
        )
    return candidate


# =============================================================================
# Configuration resolution.
#
# Dependency injection, not a module import. `app/config.py` owns the five-rung
# precedence chain in full --
# `explicit -> environment -> .env -> configuration.properties -> default` --
# and resolves it into `current_app.config` when the application factory runs
# `config.from_object(load_config(name))`. This module therefore reads the ALREADY
# RESOLVED value off the Flask config and re-implements no rung of the chain; it
# never loads `.env`, never reads `configuration.properties`, and never imports
# `app.config`. When no application context is active -- a direct programmatic
# call, or a unit test with no Flask application at all -- the cited FALLBACK_*
# constants above are used instead.
#
# `configuration.properties` is git-ignored at [.gitignore:L3] and is therefore
# ABSENT in a fresh checkout, which is the ordinary case. Every setting below has
# a documented default precisely so that costs nothing.
# =============================================================================


def _config_value(key: str) -> object | None:
    """Return ``current_app.config[key]``, or ``None`` when it cannot be read.

    ``None`` covers three situations that are all the same to a caller: there is no
    application context, the key is absent, or its value is literally ``None``. Each means
    "the Flask configuration has nothing to say about this setting", so the fallback applies.

    Args:
        key: One of ``app/config.py``'s own ``Config`` attribute names.

    Returns:
        The configured value as a plain :class:`object`, deliberately untyped here so the
        coercion helpers below can validate it instead of trusting it.
    """
    if not has_app_context():
        return None
    # Annotated as `object` on purpose: Flask's config is a `dict[str, Any]`, and
    # letting that `Any` escape would silently disable type checking downstream.
    value: object = current_app.config.get(key)
    return value


def _config_text(key: str) -> str | None:
    """Return a configured string, or ``None`` when the key is absent or not a string.

    A non-string value is treated as absent rather than coerced: a configuration layer that
    handed this stage an integer where a URL belongs has a problem the fallback cannot fix,
    and silently calling :func:`str` on it would hide that.

    Args:
        key: One of ``app/config.py``'s own ``Config`` attribute names.

    Returns:
        The configured string, which MAY be empty -- an empty value is a deliberate
        setting, not an absent one, and each caller decides what it means.
    """
    value = _config_value(key)
    if isinstance(value, str):
        return value
    if value is not None:
        _LOGGER.warning(
            "[%s] configuration key %s is not a string (%s); falling back to the "
            "documented default",
            STAGE_NAME,
            key,
            type(value).__name__,
        )
    return None


def _config_seconds(key: str) -> float | None:
    """Return a configured duration in seconds, or ``None`` when it cannot be read.

    Accepts an ``int`` or ``float`` as-is and a numeric string as a convenience, because a
    value that reached the configuration through ``configuration.properties`` may still be
    text. ``bool`` is refused explicitly: ``True`` is an ``int`` in Python, and a one-second
    timeout conjured out of a boolean would be a very confusing bug.

    Args:
        key: One of ``app/config.py``'s own ``Config`` attribute names.

    Returns:
        The duration as a float, or ``None`` when the key is absent or unusable.
    """
    value = _config_value(key)
    if isinstance(value, bool):
        _LOGGER.warning(
            "[%s] configuration key %s is a boolean, which is not a duration; falling "
            "back to the documented default",
            STAGE_NAME,
            key,
        )
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            _LOGGER.warning(
                "[%s] configuration key %s is not a number (%r); falling back to the "
                "documented default",
                STAGE_NAME,
                key,
                value,
            )
    return None


def _positive_seconds(value: float, *, source: str) -> float:
    """Return *value* once it is proved to be a usable timeout.

    Args:
        value: The candidate duration in seconds.
        source: Human-readable description of where the value came from, used in the error.

    Returns:
        *value* unchanged.

    Raises:
        CloneConfigurationError: If the value is not finite or is not greater than zero.
            Both are refused rather than repaired: a zero or negative timeout would make
            :func:`subprocess.run` report a timeout for a call that never had a chance to
            start, and an infinite one would abolish the guarantee that AAP section 0.6
            requires this stage to provide.
    """
    if not math.isfinite(value) or value <= 0.0:
        raise CloneConfigurationError(
            f"stage {STAGE_NAME!r} needs a finite, positive timeout in seconds, but "
            f"{source} supplied {value!r}. The source default is "
            f"{FALLBACK_CLONE_TIMEOUT_SECONDS:g} seconds."
        )
    return value


def _resolved_url(explicit: str | None) -> str:
    """Resolve the repository URL for this run.

    Args:
        explicit: A URL supplied by the caller, or ``None`` to resolve one.

    Returns:
        A validated URL. With nothing configured this is :data:`FALLBACK_CLONE_URL`, the
        pipeline's own address ``[Jenkins:L3]`` -- preserved defect **D7**.

    Raises:
        CloneConfigurationError: If the resolved URL is unusable. An explicitly supplied or
            explicitly configured empty value is reported rather than quietly replaced by
            the default, because it is a deliberate setting that cannot work.
    """
    if explicit is not None:
        return _validated_url(explicit)
    configured = _config_text(_CONFIG_KEY_URL)
    if configured is not None:
        return _validated_url(configured)
    return _validated_url(FALLBACK_CLONE_URL)


def _resolved_branch(explicit: str | None) -> str | None:
    """Resolve the ref to check out, or ``None`` for the remote's default branch.

    ``None`` is the faithful port of the source step: ``git '<url>'`` ``[Jenkins:L3]`` named
    no ref and therefore took whatever the remote's ``HEAD`` pointed at. Clearing the
    setting to an empty value is the documented way to restore exactly that behaviour, and
    ``app/api/schemas.py``'s ``ClonePolicy.branch`` already describes ``None`` as meaning
    "the remote default is used".

    Args:
        explicit: A ref supplied by the caller, or ``None`` to resolve one.

    Returns:
        A validated ref name, or ``None``.

    Raises:
        CloneConfigurationError: If the resolved ref is not a well-formed branch or tag name.
    """
    if explicit is not None:
        candidate = explicit
    else:
        configured = _config_text(_CONFIG_KEY_BRANCH)
        candidate = FALLBACK_CLONE_BRANCH if configured is None else configured
    stripped = candidate.strip()
    if not stripped:
        return None
    return _validated_ref(stripped)


def _resolved_timeout(explicit: float | None) -> float:
    """Resolve the whole-stage ``git`` timeout, in seconds.

    AAP section 0.6 requires this stage to "run with an explicit timeout", and
    ``app/config.py`` supplies the value: its own documentation for
    ``CLONE_TIMEOUT_SECONDS`` states that this module "always runs ``git`` under an explicit
    timeout". The constant here is the last-resort mirror for the no-application case.

    Args:
        explicit: A timeout supplied by the caller, or ``None`` to resolve one.

    Returns:
        A finite, positive number of seconds.

    Raises:
        CloneConfigurationError: If a supplied or configured value is not usable.
    """
    if explicit is not None:
        return _positive_seconds(float(explicit), source="the supplied argument")
    configured = _config_seconds(_CONFIG_KEY_TIMEOUT)
    if configured is not None:
        return _positive_seconds(configured, source=f"configuration key {_CONFIG_KEY_TIMEOUT}")
    return FALLBACK_CLONE_TIMEOUT_SECONDS


def _fallback_destination() -> Path:
    """Return the checkout directory used when no destination is configured.

    A deterministically named subdirectory of the system temporary directory, so two runs
    reuse one checkout and the already-cloned path is exercised naturally.

    :func:`tempfile.gettempdir` is called HERE rather than at import time on purpose: it
    probes candidate directories for writability, which is a filesystem side effect, and
    importing this module must have none.

    Returns:
        ``<system temporary directory>/testinium-qa-clone``.
    """
    return Path(tempfile.gettempdir()) / FALLBACK_CLONE_DIRECTORY_NAME


def _guard_destination(candidate: Path) -> None:
    """Refuse a destination that would destroy something, before anything is created.

    This is the one place in this module that could do irreversible damage, so the three
    checks below are load-bearing rather than defensive padding:

    * the project root IS the repository being ported, and cloning over it would clobber it;
    * a directory CONTAINING the project root is the same hazard one level up;
    * anywhere inside ``target/`` is pointless and destructive, because the very next
      pipeline stage, ``'Run tests'``, wipes that tree -- AAP section 0.2.2: "Everything
      under ``target/`` ... Ephemeral by design; the clean step wipes it each run."

    Nothing here creates, renames or deletes ``target/``; the name is obtained from
    ``app/utils/paths.py``, the module that owns it, and is never spelled out as a literal.

    Args:
        candidate: An absolute, normalised destination path.

    Raises:
        CloneConfigurationError: If the destination is unsafe.
    """
    if candidate == _PROJECT_ROOT:
        raise CloneConfigurationError(
            f"stage {STAGE_NAME!r} refuses to check out into {candidate}: that directory is "
            "the repository being ported, and cloning over it would destroy it. Point "
            f"{_CONFIG_KEY_DIRECTORY} somewhere else."
        )
    if _PROJECT_ROOT.is_relative_to(candidate):
        raise CloneConfigurationError(
            f"stage {STAGE_NAME!r} refuses to check out into {candidate}: that directory "
            "contains the repository being ported. Point "
            f"{_CONFIG_KEY_DIRECTORY} at a directory that does not."
        )
    if candidate.is_relative_to(_TARGET_ROOT):
        raise CloneConfigurationError(
            f"stage {STAGE_NAME!r} refuses to check out into {candidate}: it lies inside "
            f"the ephemeral artifact root {_TARGET_ROOT}, which stage 'Run tests' wipes "
            f"immediately afterwards. Point {_CONFIG_KEY_DIRECTORY} outside it."
        )


def _resolved_destination(explicit: StrPath | None) -> Path:
    """Resolve and guard the directory the repository is checked out into.

    A relative value -- which is how both ``app/config.py``'s ``CLONE_DIR`` ("relative to
    the repository root") and ``app/api/schemas.py``'s ``CloneRequest.directory`` are
    defined -- is joined onto the project root, never onto the process working directory:
    the same configuration must resolve identically under ``flask run``, under gunicorn from
    a container ``WORKDIR`` and under pytest from a subdirectory.

    Args:
        explicit: A destination supplied by the caller, or ``None`` to resolve one.

    Returns:
        An absolute, symlink-resolved directory path that has passed :func:`_guard_destination`.

    Raises:
        CloneConfigurationError: If the destination is empty or unsafe. Both the
            textually normalised form and the symlink-resolved form are guarded, so a
            symlink cannot smuggle a destination into ``target/``.
    """
    if explicit is not None:
        candidate = os.fspath(explicit)
    else:
        configured = _config_text(_CONFIG_KEY_DIRECTORY)
        if configured is None:
            candidate = os.fspath(_fallback_destination())
        elif not configured.strip():
            raise CloneConfigurationError(
                f"stage {STAGE_NAME!r} was configured with an empty {_CONFIG_KEY_DIRECTORY}. "
                "Give it a directory, or remove the setting to use "
                f"<system temporary directory>/{FALLBACK_CLONE_DIRECTORY_NAME}."
            )
        else:
            candidate = configured
    stripped = candidate.strip()
    if not stripped:
        raise CloneConfigurationError(
            f"stage {STAGE_NAME!r} was given an empty checkout destination."
        )
    absolute = Path(stripped).expanduser()
    if not absolute.is_absolute():
        absolute = _PROJECT_ROOT / absolute
    normalised = Path(os.path.normpath(absolute))
    resolved = normalised.resolve()
    _guard_destination(normalised)
    if resolved != normalised:
        _guard_destination(resolved)
    return resolved


# =============================================================================
# Running git.
#
# BASELINE B6, the security constraint of this package, is enforced entirely in
# this section: a `list[str]` argument list, no shell, an explicit timeout on
# every single call, an environment that cannot start an interactive prompt, a
# closed standard input, and UTF-8 decoding declared rather than guessed.
# =============================================================================


@dataclass(slots=True)
class _TimeBudget:
    """The whole-stage timeout, shared by every ``git`` call the stage makes.

    A stage may need up to four ``git`` invocations. Handing each of them the full configured
    timeout would let the stage run for four times its budget, so the budget is tracked once
    and each call receives what is left of it. That is what makes "the stage runs under an
    explicit timeout" true of the STAGE and not merely of each call in isolation.

    Measured on :func:`time.monotonic`, so a clock adjustment during a long clone cannot
    make the budget jump backwards or forwards.
    """

    started: float
    """Value of :func:`time.monotonic` when the stage began."""

    limit: float
    """Total seconds the stage is allowed, resolved by :func:`_resolved_timeout`."""

    @property
    def elapsed(self) -> float:
        """Return the seconds spent so far."""
        return time.monotonic() - self.started

    @property
    def remaining(self) -> float:
        """Return the seconds left, which may be zero or negative."""
        return self.limit - self.elapsed

    @property
    def expired(self) -> bool:
        """Return whether the budget is used up."""
        return self.remaining <= 0.0

    def next_timeout(self) -> float:
        """Return the timeout to hand the next subprocess.

        Never zero or negative: :func:`subprocess.run` treats those as "already timed out"
        and would report a timeout for a call that never started, so the last sliver of
        budget is rounded up to :data:`_MINIMUM_CALL_TIMEOUT_SECONDS`. Callers check
        :attr:`expired` first, so this floor only ever applies to a call that raced the
        deadline by microseconds.
        """
        return max(self.remaining, _MINIMUM_CALL_TIMEOUT_SECONDS)


@dataclass(frozen=True, slots=True)
class _GitStep:
    """One ``git`` invocation: what to run, where to run it, and what to call it.

    The argument list is a tuple so a step cannot be mutated between being built and being
    executed; :func:`_run_git` copies it into a fresh list for :func:`subprocess.run`.
    """

    label: str
    """Human-readable name of the step, used in log records and failure messages."""

    arguments: tuple[str, ...]
    """The complete argv, ``git`` executable first. Never a command string."""

    working_directory: Path
    """Directory to run in. Always explicit -- the ambient working directory is never used."""


def _git_environment() -> dict[str, str]:
    """Return the environment every ``git`` invocation runs with.

    Inherits the process environment -- ``git`` legitimately needs ``HOME``, ``PATH``,
    ``SSH_AUTH_SOCK`` and the proxy variables -- and then forces the prompt-related
    settings. Without this, a repository needing credentials would make ``git`` sit waiting
    for input until the timeout expired, turning a fast, clear authentication failure into a
    slow, opaque one. That is why AAP section 0.6 pairs "explicit timeout" with this
    hardening.

    Returns:
        A fresh mapping. Building a copy rather than mutating :data:`os.environ` keeps the
        change local to the child process.
    """
    environment = dict(os.environ)
    environment.update(_GIT_TERMINAL_PROMPT_DISABLED)
    return environment


def _run_git(
    arguments: Sequence[str], *, cwd: Path, timeout_seconds: float
) -> subprocess.CompletedProcess[str]:
    """Execute one ``git`` command from an argument list, under an explicit timeout.

    The single choke point through which every ``git`` invocation in this module passes, so
    baseline B6 is satisfied in one auditable place:

    * the first argument is a ``list[str]`` of separate argv tokens, so no shell is involved
      and no quoting or escaping can go wrong;
    * the shell keyword is never enabled, none of the shell-invoking standard-library
      helpers is used, and no command string is built by joining tokens together -- not
      here, and nowhere else in this file;
    * ``timeout`` is always supplied, so no call can block for ever;
    * standard input is ``DEVNULL``, so nothing can read from the terminal;
    * output is decoded as UTF-8 explicitly with ``errors="replace"`` (baseline B7), so a
      commit message or a filename containing non-ASCII text cannot raise
      :exc:`UnicodeDecodeError` and abort the stage;
    * ``check=False``, because a non-zero exit is a value this module reports, not an
      exception it lets escape.

    The debug record logs the REDACTED argument list, never the raw one.

    Args:
        arguments: The complete argv, ``git`` executable first.
        cwd: Directory to run in.
        timeout_seconds: Seconds to allow, always positive.

    Returns:
        The completed process, with both streams captured as text.

    Raises:
        subprocess.TimeoutExpired: If the command outlived *timeout_seconds*.
        OSError: If the command could not be started at all -- for example
            :exc:`FileNotFoundError` when the executable vanished between the ``PATH`` probe
            and the call. Both are translated into structured results by :func:`clone_code`.
    """
    _LOGGER.debug(
        "[%s] running %s in %s under a %.3fs timeout",
        STAGE_NAME,
        _redacted_arguments(arguments),
        cwd,
        timeout_seconds,
    )
    return subprocess.run(
        list(arguments),
        cwd=cwd,
        env=_git_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )


def _log_streams(completed: subprocess.CompletedProcess[str], *, url: str) -> None:
    """Log a completed command's captured streams at debug level, redacted.

    Args:
        completed: The finished process.
        url: The repository URL whose credentials must not reach the log.
    """
    for name, text in (("stdout", completed.stdout), ("stderr", completed.stderr)):
        content = (text or "").strip()
        if content:
            _LOGGER.debug("[%s] git %s: %s", STAGE_NAME, name, _redact_text(content, url=url))


def _failure_detail(completed: subprocess.CompletedProcess[str], *, url: str) -> str:
    """Summarise why a ``git`` command failed, in one redacted line.

    Prefers ``stderr``, which is where git writes its ``fatal:`` lines, and falls back to
    ``stdout`` when stderr is empty.

    Args:
        completed: The finished, non-zero process.
        url: The repository URL whose credentials must not reach the message.

    Returns:
        A bounded single-line summary, or ``""`` when git said nothing at all.
    """
    raw = (completed.stderr or "").strip() or (completed.stdout or "").strip()
    return _summarise(_redact_text(raw, url=url))


# =============================================================================
# Deciding what to do with the destination.
#
# AAP section 0.6 requires the already-cloned case to be "handled by fetching and
# resetting rather than failing", so this is where "clone" and "update" are told
# apart -- and where a destination holding somebody else's files is refused
# instead of being clobbered.
# =============================================================================


class _Plan(StrEnum):
    """Which of the three courses of action the destination calls for."""

    CLONE = "clone"
    """Absent or empty: make a fresh clone."""

    UPDATE = "update"
    """Already a checkout of the requested repository: fetch and reset."""

    CONFLICT = "conflict"
    """Occupied by something else, or unreadable: refuse, and change nothing."""


@dataclass(frozen=True, slots=True)
class _DestinationPlan:
    """A :class:`_Plan` and, for a conflict, the reason for it."""

    plan: _Plan
    """The course of action."""

    detail: str = ""
    """Why, for :attr:`_Plan.CONFLICT`. Empty for the other two."""


def _is_empty_directory(path: Path) -> bool:
    """Return whether *path* is a directory containing nothing at all.

    An empty directory is treated as a fresh destination, because ``git clone`` accepts one
    and because a deployment that pre-creates its checkout directory -- a mounted volume,
    for instance -- must not be told its destination is occupied.

    Args:
        path: An existing directory.

    Returns:
        ``True`` when the directory holds no entries, not even hidden ones.

    Raises:
        OSError: If the directory cannot be read. Handled by :func:`_plan_for`.
    """
    with os.scandir(path) as entries:
        return next(entries, None) is None


def _git_config_path(destination: Path) -> Path | None:
    """Return the path of an existing checkout's ``config`` file, or ``None``.

    Handles both shapes git uses. An ordinary clone has a ``.git`` DIRECTORY; a linked
    worktree or a submodule has a ``.git`` FILE holding ``gitdir: <path>``. Supporting the
    second shape matters: refusing it would report a destination conflict for a perfectly
    valid checkout.

    Args:
        destination: The candidate checkout directory.

    Returns:
        The config file's path, which is not checked for existence here, or ``None`` when
        *destination* is not a git checkout at all.

    Raises:
        OSError: If the ``.git`` file cannot be read. Handled by :func:`_plan_for`.
    """
    marker = destination / _GIT_DIRECTORY_NAME
    if marker.is_dir():
        return marker / _GIT_CONFIG_NAME
    if not marker.is_file():
        return None
    with open(marker, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped.startswith(_GIT_FILE_PREFIX):
                continue
            target = stripped[len(_GIT_FILE_PREFIX) :].strip()
            if not target:
                return None
            git_directory = Path(target)
            if not git_directory.is_absolute():
                git_directory = destination / git_directory
            return git_directory / _GIT_CONFIG_NAME
    return None


def _is_origin_section(header: str) -> bool:
    """Return whether a git-config section header names the ``origin`` remote.

    Git section names are case-insensitive while subsection names are case-sensitive, and
    this mirrors that exactly, so ``[REMOTE "origin"]`` is recognised and
    ``[remote "Origin"]`` is not.

    Args:
        header: The text between the brackets, for example ``remote "origin"``.

    Returns:
        ``True`` for the ``origin`` remote's section.
    """
    name, _, subsection = header.partition(" ")
    if name.strip().lower() != _REMOTE_SECTION_NAME:
        return False
    return subsection.strip().strip('"') == _ORIGIN_REMOTE


def _origin_url(config_path: Path) -> str | None:
    """Read the ``origin`` remote's URL out of a git config file.

    Read as TEXT rather than through ``git remote get-url`` or ``git config`` deliberately:
    this module is permitted exactly four git subcommands -- ``clone``, ``fetch``, ``reset``
    and ``rev-parse`` -- and parsing one well-known line needs no fifth. Baseline B7's
    explicit UTF-8, with ``errors="replace"``, means an oddly encoded config cannot raise.

    Args:
        config_path: Path of the checkout's ``config`` file.

    Returns:
        The first ``url`` entry of the ``[remote "origin"]`` section, or ``None`` when the
        file has no such entry.

    Raises:
        OSError: If the file cannot be read. Handled by :func:`_plan_for`.
    """
    inside_origin = False
    with open(config_path, encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line[0] in "#;":
                continue
            if line.startswith("["):
                closing = line.find("]")
                if closing == -1:
                    continue
                inside_origin = _is_origin_section(line[1:closing])
                # Git allows a key on the same line as its section header, so the
                # remainder is examined rather than discarded.
                line = line[closing + 1 :].strip()
                if not line:
                    continue
            if not inside_origin or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip().lower() == _URL_KEY_NAME:
                return value.strip()
    return None


def _is_scp_like(value: str) -> bool:
    """Return whether *value* is git's scp-like ``[user@]host:path`` syntax.

    Applies git's own rule: the value is scp-like when it contains a colon that comes before
    any slash. ``host:path`` therefore is, and ``/srv/repos:odd/name`` is not.

    Args:
        value: An unschemed repository URL or local path.

    Returns:
        ``True`` for the scp-like form.
    """
    colon = value.find(":")
    if colon == -1:
        return False
    slash = value.find("/")
    return slash == -1 or colon < slash


def _comparable_local_path(value: str) -> str:
    """Return a canonical spelling of a local repository path, for comparison only.

    Args:
        value: A filesystem path, possibly using ``~`` or symlinks.

    Returns:
        The expanded, symlink-resolved absolute path.
    """
    return os.path.realpath(os.path.expanduser(value))


def _comparable_remote(value: str) -> str:
    """Return a canonical form of a remote URL, used ONLY to compare two remotes.

    Never stored, never logged and never handed to git: the value git receives is always the
    unmodified configured one, because AAP Rule T1 forbids normalising a preserved
    configuration value. This canonical form exists so that "is the checkout already here
    the one that was asked for?" has a sensible answer, and it deliberately ignores three
    differences that do not change which repository is meant:

    * embedded credentials, so a URL whose authority carries a userinfo component
      matches the same URL without one;
    * a trailing slash, and a ``.git`` suffix on a network URL;
    * host letter case, which DNS treats as insignificant. The path's case is preserved,
      because forge paths are case-sensitive.

    Local paths are canonicalised through the filesystem instead, so ``file:///srv/x.git``
    matches ``/srv/x.git``. A ``.git`` suffix is NOT stripped for them: ``/srv/x.git`` and
    ``/srv/x`` are two different directories. The scp-like form is compared verbatim, since
    there is no reliable way to canonicalise it further; the cost of being wrong is a
    refusal to touch the destination, which is the safe direction.

    Args:
        value: A remote URL, as configured or as read out of ``.git/config``.

    Returns:
        A canonical string. Two values that name the same repository compare equal.
    """
    candidate = value.strip()
    if not candidate:
        return ""
    try:
        split = urlsplit(candidate)
    except ValueError:
        return candidate
    if split.scheme == "file":
        return _comparable_local_path(split.path)
    if not split.scheme:
        return candidate if _is_scp_like(candidate) else _comparable_local_path(candidate)
    netloc = split.netloc
    marker = netloc.rfind("@")
    if marker != -1:
        netloc = netloc[marker + 1 :]
    path = split.path.rstrip("/")
    if path.endswith(_GIT_URL_SUFFIX):
        path = path[: -len(_GIT_URL_SUFFIX)]
    return f"{split.scheme}://{netloc.lower()}{path}"


def _plan_for(destination: Path, url: str) -> _DestinationPlan:
    """Decide what to do with *destination*, without modifying anything.

    This is the direct implementation of AAP section 0.6's central requirement: an existing
    checkout of the requested repository is a NORMAL case to be updated, not an error. The
    source stage behaved that way because Jenkins re-ran it against a workspace that usually
    already held the repository.

    Args:
        destination: The absolute, guarded destination directory.
        url: The validated repository URL that was requested.

    Returns:
        :attr:`_Plan.CLONE` when the destination is absent or empty; :attr:`_Plan.UPDATE`
        when it already holds a checkout whose ``origin`` names the same repository;
        :attr:`_Plan.CONFLICT`, with a reason, for anything else. A conflict is never
        resolved by deleting what is there: AAP section 0.6 prescribes fetch-and-reset for an
        existing checkout and says nothing about removing a stranger's files, and AAP Rule T6
        forbids inventing behaviour the plan does not prescribe.
    """
    try:
        if not destination.exists():
            return _DestinationPlan(_Plan.CLONE)
        if not destination.is_dir():
            return _DestinationPlan(
                _Plan.CONFLICT, "the destination already exists and is not a directory"
            )
        if _is_empty_directory(destination):
            return _DestinationPlan(_Plan.CLONE)
        config_path = _git_config_path(destination)
        if config_path is None:
            return _DestinationPlan(
                _Plan.CONFLICT,
                "the destination is a non-empty directory that is not a git checkout",
            )
        origin = _origin_url(config_path)
        if origin is None:
            return _DestinationPlan(
                _Plan.CONFLICT,
                f"the destination is a git checkout with no {_ORIGIN_REMOTE!r} remote URL",
            )
        if _comparable_remote(origin) != _comparable_remote(url):
            return _DestinationPlan(
                _Plan.CONFLICT,
                f"the destination is a checkout of {redact_url(origin)}, which is not the "
                "repository that was requested",
            )
        return _DestinationPlan(_Plan.UPDATE)
    except OSError as error:
        return _DestinationPlan(
            _Plan.CONFLICT, f"the destination could not be inspected ({error.strerror or error})"
        )


# =============================================================================
# Building the four permitted commands.
#
# `clone`, `fetch`, `reset` and `rev-parse`. Nothing writes to a remote -- AAP
# section 0.2.2: the two remote repositories "are referenced as clone targets and
# never modified" -- and `git clean` appears nowhere, so an untracked file sitting
# beside the checkout survives an update untouched.
# =============================================================================


def _clone_step(executable: str, *, url: str, destination: Path, ref: str | None) -> _GitStep:
    """Build the fresh-clone step: the direct port of ``git '<url>'`` ``[Jenkins:L3]``.

    The URL follows ``--``, git's documented end-of-options separator for ``clone``, so it
    is data even if it somehow began with a dash -- which :func:`_validated_url` has already
    refused anyway. Omitting the ref reproduces the source step exactly: it named none and
    took the remote's default branch.

    Args:
        executable: Absolute path of the ``git`` binary, from :func:`shutil.which`.
        url: The validated repository URL.
        destination: Absolute destination directory.
        ref: Branch or tag to check out, or ``None`` for the remote's default.

    Returns:
        The step, ready to run in the destination's PARENT directory -- which must exist,
        and which the caller creates.
    """
    arguments: list[str] = [executable, _SUBCOMMAND_CLONE]
    if ref is not None:
        arguments += [_BRANCH_FLAG, ref]
    arguments += [_END_OF_OPTIONS, url, str(destination)]
    return _GitStep(
        label=f"git {_SUBCOMMAND_CLONE}",
        arguments=tuple(arguments),
        working_directory=destination.parent,
    )


def _update_steps(executable: str, *, destination: Path, ref: str | None) -> tuple[_GitStep, ...]:
    """Build the already-cloned steps: fetch, then reset hard onto what was fetched.

    Exactly the two operations AAP section 0.6 names -- "handle the already-cloned case by
    fetching and resetting" -- and deliberately nothing more aggressive. ``FETCH_HEAD`` is
    the ref the fetch just wrote, which is why a single named ref is fetched rather than the
    remote's whole default refspec: one fetched ref makes ``FETCH_HEAD`` unambiguous. With no
    ref configured, ``HEAD`` asks the remote for its own default branch, reproducing the
    ref-less source step.

    Args:
        executable: Absolute path of the ``git`` binary.
        destination: Absolute directory of the existing checkout.
        ref: Branch or tag to update to, or ``None`` for the remote's default.

    Returns:
        The two steps, in order, both running inside the checkout.
    """
    fetch_ref = _HEAD_REF if ref is None else ref
    fetch = _GitStep(
        label=f"git {_SUBCOMMAND_FETCH}",
        arguments=(executable, _SUBCOMMAND_FETCH, _ORIGIN_REMOTE, fetch_ref),
        working_directory=destination,
    )
    reset = _GitStep(
        label=f"git {_SUBCOMMAND_RESET}",
        arguments=(executable, _SUBCOMMAND_RESET, _HARD_RESET_FLAG, _FETCH_HEAD_REF),
        working_directory=destination,
    )
    return (fetch, reset)


def _head_commit(
    executable: str, destination: Path, *, budget: _TimeBudget, url: str
) -> str | None:
    """Return the commit the checkout ended up at, or ``None`` when it cannot be read.

    Reported so a caller can correlate a test run with the exact checkout it ran against.
    Never fatal: an empty repository legitimately has no ``HEAD``, and the clone or update
    itself has already succeeded by the time this runs, so a failure here is logged and the
    commit is simply omitted.

    Args:
        executable: Absolute path of the ``git`` binary.
        destination: The checkout directory.
        budget: The remaining whole-stage timeout.
        url: The repository URL whose credentials must not reach the log.

    Returns:
        The 40-character commit SHA, or ``None``.
    """
    if budget.expired:
        _LOGGER.warning(
            "[%s] no time left to read %s; the commit is not reported", STAGE_NAME, _HEAD_REF
        )
        return None
    arguments = (executable, _SUBCOMMAND_REV_PARSE, _HEAD_REF)
    try:
        completed = _run_git(arguments, cwd=destination, timeout_seconds=budget.next_timeout())
    except (OSError, subprocess.SubprocessError) as error:
        _LOGGER.warning(
            "[%s] %s could not be read: %s",
            STAGE_NAME,
            _HEAD_REF,
            _redact_text(str(error), url=url),
        )
        return None
    if completed.returncode != 0:
        _LOGGER.warning(
            "[%s] %s could not be read: git exited %d (%s)",
            STAGE_NAME,
            _HEAD_REF,
            completed.returncode,
            _failure_detail(completed, url=url),
        )
        return None
    return completed.stdout.strip() or None


# =============================================================================
# The stage.
# =============================================================================


def clone_code(
    *,
    url: str | None = None,
    branch: str | None = None,
    destination: StrPath | None = None,
    timeout_seconds: float | None = None,
) -> CloneResult:
    """Run stage ``'Clone code'`` -- the Python port of ``git '<url>'`` ``[Jenkins:L3]``.

    Keyword-only, and every parameter optional: passing nothing reproduces the source
    pipeline's behaviour, in which every value was fixed in the pipeline script. An unset
    parameter is read off ``flask.current_app.config`` when an application context is active
    and falls back to this module's cited constants otherwise, so the function works
    identically inside a request, inside the orchestrator and in a unit test with no Flask
    application at all.

    What it does, in order:

    1. Resolves and VALIDATES every setting. A misconfiguration raises before any process
       exists, so nothing has happened when it does.
    2. Confirms ``git`` is on ``PATH``.
    3. Inspects the destination. Absent or empty means clone; an existing checkout of the
       same repository means fetch-and-reset, which AAP section 0.6 requires to SUCCEED
       rather than fail; anything else is refused without being touched.
    4. Runs the necessary ``git`` commands from argument lists, sharing one whole-stage
       timeout between them.
    5. Reads ``HEAD`` so the caller can correlate a run with a checkout.

    Args:
        url: Repository to clone. Unset resolves ``CLONE_URL``, whose source default is the
            pipeline's ``https://github.com/BalamiRR/Upgenix-QA.git`` ``[Jenkins:L3]``. The
            README names a DIFFERENT repository ``[README.md:L59]``; that is preserved defect
            **D7**, the pipeline value wins because executable configuration outranks prose
            (AAP AMB-7), and both strings survive -- see the banner beside
            :data:`FALLBACK_CLONE_URL`.
        branch: Ref to check out. Unset resolves ``CLONE_BRANCH`` (source default ``main``).
            An EMPTY value means "the remote's default branch", which is exactly what the
            ref-less Groovy step took.
        destination: Checkout directory. Unset resolves ``CLONE_DIR``. A relative value is
            joined onto the repository root -- never onto the process working directory --
            and with nothing configured at all the checkout goes to
            ``<system temporary directory>/testinium-qa-clone``, outside this repository.
        timeout_seconds: Whole-stage timeout. Unset resolves ``CLONE_TIMEOUT_SECONDS``
            (source default ``300``). Every ``git`` call shares this one budget, so the stage
            cannot outlive it however many calls it makes.

    Returns:
        A :class:`CloneResult`. ``success=True`` carries
        :attr:`CloneAction.CLONED` or :attr:`CloneAction.UPDATED`; ``success=False`` carries
        :attr:`CloneAction.FAILED` and a :class:`CloneErrorCode`. An operational failure is a
        DOMAIN outcome and is never raised, so an HTTP caller answers it with ``200`` and a
        body -- the same rule ``app/errors.py`` applies to a zero-scenario test run. The URL
        in the result is always redacted.

    Raises:
        CloneConfigurationError: If the resolved configuration cannot be acted on -- an empty
            or dangerous URL, a malformed ref, a non-positive timeout, or a destination that
            is the repository root, contains it, or lies inside the ephemeral ``target/``
            tree. Always raised BEFORE any subprocess is created.

    Example:
        Outside a Flask application context, with everything supplied explicitly::

            result = clone_code(
                url="file:///srv/mirrors/upgenix-qa.git",
                branch="main",
                destination="/var/lib/testinium-qa/checkout",
                timeout_seconds=60,
            )
            if result.success:
                logger.info("checked out %s at %s", result.action, result.commit)
            else:
                logger.error("clone stage failed: %s", result.error_code)
    """
    started = time.monotonic()

    # Resolution and validation first, so a misconfigured deployment is reported
    # before anything at all is executed or created.
    resolved_url = _resolved_url(url)
    resolved_branch = _resolved_branch(branch)
    resolved_timeout = _resolved_timeout(timeout_seconds)
    resolved_destination = _resolved_destination(destination)

    safe_url = redact_url(resolved_url)
    destination_text = to_posix(resolved_destination)
    budget = _TimeBudget(started=started, limit=resolved_timeout)

    def failed(code: CloneErrorCode, message: str, *, returncode: int | None = None) -> CloneResult:
        """Build, log and return the failure result for *code*."""
        outcome = CloneResult(
            success=False,
            action=CloneAction.FAILED,
            url=safe_url,
            destination=destination_text,
            branch=resolved_branch,
            commit=None,
            duration_seconds=budget.elapsed,
            timeout_seconds=resolved_timeout,
            returncode=returncode,
            error_code=code,
            message=message,
        )
        _LOGGER.error(
            "[%s] failed after %.3fs [%s]: %s",
            STAGE_NAME,
            outcome.duration_seconds,
            code.value,
            message,
        )
        return outcome

    _LOGGER.info(
        "[%s] starting: url=%s branch=%s destination=%s timeout=%.3fs",
        STAGE_NAME,
        safe_url,
        "<remote default>" if resolved_branch is None else resolved_branch,
        destination_text,
        resolved_timeout,
    )

    # The runtime image installs `git` for exactly this stage, so a missing binary
    # is an incomplete environment and is reported as such -- never as a bare
    # FileNotFoundError traceback.
    executable = shutil.which(_GIT_EXECUTABLE)
    if executable is None:
        return failed(
            CloneErrorCode.GIT_NOT_FOUND,
            f"the {_GIT_EXECUTABLE!r} executable was not found on PATH, so the repository "
            f"cannot be checked out; install {_GIT_EXECUTABLE} in the runtime environment.",
        )

    plan = _plan_for(resolved_destination, resolved_url)
    if plan.plan is _Plan.CONFLICT:
        return failed(
            CloneErrorCode.DESTINATION_CONFLICT,
            f"the checkout destination cannot be used because {plan.detail}; nothing was "
            "modified, and no existing file was removed.",
        )

    steps: tuple[_GitStep, ...]
    action: CloneAction
    success_message: str
    if plan.plan is _Plan.CLONE:
        # `git clone` would create the leading directories itself, but the parent is
        # created here anyway so the step can be run with a deliberate, existing
        # working directory rather than with whatever the process happens to have.
        try:
            resolved_destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            return failed(
                CloneErrorCode.DESTINATION_CONFLICT,
                "the parent of the checkout destination could not be created "
                f"({error.strerror or error}); nothing was modified.",
            )
        steps = (
            _clone_step(
                executable, url=resolved_url, destination=resolved_destination, ref=resolved_branch
            ),
        )
        action = CloneAction.CLONED
        success_message = "Repository cloned into a fresh destination."
    else:
        steps = _update_steps(executable, destination=resolved_destination, ref=resolved_branch)
        action = CloneAction.UPDATED
        success_message = (
            "Existing checkout fetched and reset. An already-cloned destination is the "
            "normal case for this stage, not an error."
        )

    returncode: int | None = None
    for step in steps:
        if budget.expired:
            return failed(
                CloneErrorCode.TIMEOUT,
                f"the {resolved_timeout:g}s stage timeout expired before {step.label} could "
                "be started.",
                returncode=returncode,
            )
        try:
            completed = _run_git(
                step.arguments,
                cwd=step.working_directory,
                timeout_seconds=budget.next_timeout(),
            )
        except subprocess.TimeoutExpired:
            # Reported, never propagated: AAP section 0.6's explicit timeout is a
            # stage outcome, not a 500.
            return failed(
                CloneErrorCode.TIMEOUT,
                f"{step.label} did not finish within the {resolved_timeout:g}s stage timeout "
                "and was terminated.",
                returncode=returncode,
            )
        except (FileNotFoundError, NotADirectoryError) as error:
            # Belt and braces: the PATH probe above succeeded, so the executable was
            # removed or replaced between then and now.
            return failed(
                CloneErrorCode.GIT_NOT_FOUND,
                f"the {_GIT_EXECUTABLE!r} executable could not be run for {step.label} "
                f"({error.strerror or error}); it appears to have been removed or replaced "
                "after the PATH probe succeeded.",
                returncode=returncode,
            )
        except OSError as error:
            return failed(
                CloneErrorCode.GIT_FAILED,
                f"{step.label} could not be started ({error.strerror or error}).",
                returncode=returncode,
            )
        returncode = completed.returncode
        _log_streams(completed, url=resolved_url)
        if returncode != 0:
            detail = _failure_detail(completed, url=resolved_url)
            return failed(
                CloneErrorCode.GIT_FAILED,
                f"{step.label} exited {returncode}" + (f": {detail}" if detail else " silently."),
                returncode=returncode,
            )

    commit = _head_commit(executable, resolved_destination, budget=budget, url=resolved_url)
    result = CloneResult(
        success=True,
        action=action,
        url=safe_url,
        destination=destination_text,
        branch=resolved_branch,
        commit=commit,
        duration_seconds=budget.elapsed,
        timeout_seconds=resolved_timeout,
        returncode=returncode,
        error_code=None,
        message=success_message,
    )
    _LOGGER.info(
        "[%s] finished in %.3fs: action=%s url=%s destination=%s commit=%s",
        STAGE_NAME,
        result.duration_seconds,
        result.action.value,
        safe_url,
        destination_text,
        "<unknown>" if commit is None else commit,
    )
    return result
