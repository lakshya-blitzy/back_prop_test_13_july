"""Request and response data contracts of the ``/api/v1`` blueprint.

This module is the API layer's schema surface and nothing else: it declares the
shapes that cross the HTTP boundary, validates what arrives, and fixes how what
leaves is spelled. It defines no route, builds no application, opens no file,
starts no process, reads no environment variable and configures no logging.
Importing it has no observable effect beyond binding names.

It is also the single home of ``pydantic`` in the whole application.
``app/extensions.py`` refuses the library explicitly -- pydantic is a plain
validation library rather than a Flask extension with an ``init_app`` lifecycle
-- and the dependency manifest annotates the pin accordingly: ``pydantic``
2.13.4, "request and response schema validation in app/api/schemas.py". Every
model below is written against pydantic **v2** semantics; the v1 compatibility
shim is never used.

Why this module exists at all
-----------------------------
It is ADDITIVE. The source system was a Java/Maven Selenium and Cucumber
skeleton driven by a Groovy *scripted* Jenkins pipeline; it had no HTTP server,
so there was no request or response shape to port. The migration plan binds this
file as ``app/api/schemas.py | CREATE | ADDITIVE | "Request and response
validation"``, and it exists because the deliverable is mandated to be a Python 3
Flask application -- Flask is a requirement, not one option among several. The
HTTP surface it describes was therefore *derived* mechanically from the three
pipeline stages plus the four report artifacts, never invented: every model below
serves one of the eight endpoints of a closed route inventory.

The eight endpoints, and what each needs from this module
--------------------------------------------------------
====================================================  ============================
Endpoint                                              Contract declared here
====================================================  ============================
``GET  /api/v1/config``                               :class:`ConfigResponse`
``POST /api/v1/clone``                                :class:`CloneRequest`,
                                                      :class:`CloneResponse`
``POST /api/v1/runs``                                 :class:`RunRequest`,
                                                      :class:`RunResponse`
``GET  /api/v1/runs/<run_id>``                        :data:`RunId`,
                                                      :class:`RunStatusResponse`
``POST /api/v1/reports``                              :class:`ReportRequest`,
                                                      :class:`ReportResponse`
``GET  /api/v1/reports/<run_id>/cucumber.json``       :data:`FEATURE_KEYS` and
                                                      the other key sets
``GET  /api/v1/reports/<run_id>/rerun.txt``           nothing -- plain text
``GET  /api/v1/reports/<run_id>/screenshots``         :class:`ShotIndexResponse`
====================================================  ============================

``GET /health`` belongs to the application factory and ``GET /`` and
``GET /reports`` belong to the root-mounted web blueprint, so none of the three
is modelled here. No ninth shape is added: no feature may be dropped from the
source system and none may be added to it.

Every request field is optional, deliberately
---------------------------------------------
The application resolves a setting through five rungs, in this order::

    explicit argument -> environment variable -> .env -> configuration.properties
    -> hard-coded source default

An unset request field is what lets a value fall through to the next rung, so a
request model that *required* anything would sever the chain and hide the
preserved source defaults behind a mandatory override. Consequently:

* every field of every request model is optional;
* an empty body ``{}``, a JSON ``null`` body and no body at all are all valid and
  all mean "use the resolved configuration" -- see :func:`load_request`;
* unknown fields are **rejected**, not ignored, so a mistyped override surfaces
  as a validation error instead of silently producing a surprising default run.

Who owns which value
--------------------
This module carries values; with two exceptions it does not decide them, and it
never restates a value another module owns. That rule is what keeps the
configuration-introspection endpoint honest.

* ``app/reporting/thresholds.py`` owns the eight report-publication constants.
  The six thresholds, the sort order and the include pattern are **imported** from
  it and used as field defaults, so those numbers and strings exist in exactly one
  place in the codebase and this module cannot drift from it.
* ``app/config.py`` owns the clone URL, the tag expression, the failure-tolerance
  switch, the worker allocation, the timeouts and the artifact paths. Response
  fields for those are **required with no local default**: the route must pass in
  what configuration resolved, so this module can never answer with a stale copy.
  Their source values are cited in the field documentation for auditability, which
  is documentation rather than a second declaration.
* ``app/utils/paths.py`` owns the artifact root and every report path. This module
  declares no path literal whatsoever and carries paths only as caller-supplied
  POSIX strings.
* The two exceptions this module does own, because nothing else does, are
  :data:`DISABLED_THREAD_COUNT` -- the tuning value the source kept commented out
  -- and the Cucumber JSON key sets, which are a published API contract.

Preserved defects these shapes must accommodate
-----------------------------------------------
The rewrite is required to match the behaviour *and the logic* of the source
implementation, so its defects are behaviour and are preserved as defaults rather
than silently corrected. ``docs/migration-parity.md`` is the authoritative
register of defects D1 through D9 and of the switch that opts into each available
fix. Three of them shape this module directly:

* **D2 -- the documented default run selects zero scenarios.** The preserved tag
  expression comes from ``tags = "@LogOut"`` ``[README.md:L87]``, and no scenario
  carries that tag. pytest therefore exits ``5`` and that is a **success**.
  :class:`TestRunOutcome` gives the outcome its own name so a zero-scenario run is
  legible rather than mistaken for a failure.
* **D3 -- the build can never fail.** ``<testFailureIgnore>true</testFailureIgnore>``
  ``[pom.xml:L25]`` plus six ``-1`` publisher thresholds ``[Jenkins:L15]`` mean
  failures never gate. :class:`RunResponse` can therefore report ``succeeded`` and
  a non-zero failure count at the same time, and nothing here derives or implies a
  gate.
* **D7 -- two contradictory clone URLs.** The pipeline clones the ``Upgenix-QA``
  repository ``[Jenkins:L3]`` while the README instructs cloning ``Testinium-QA``
  ``[README.md:L59]``. Executable configuration outranks prose, so the pipeline URL
  is the runtime default; :class:`ClonePolicy` carries **both**, because the
  discrepancy is documented rather than silently unified.

Defects D1, D4, D5 and D9 concern the Gherkin specification and its execution.
Fixing them is out of scope, and nothing here models a correction to any of them:
in particular no model carries the expected-message literal of ``[README.md:L135]``,
which belongs to the test harness.

Security boundaries
-------------------
* **No secret is modelled.** There is no field for a secret key, a password, a
  token, an API key or any other credential anywhere in this module, and
  :class:`ConfigResponse` deliberately has no place to put one. Configuration
  comes from the environment and an optional properties file; secrets are never
  hard-coded and never echoed back over HTTP.
* **Any URL a model carries is already redacted.** ``app/services/clone_service.py``
  owns redaction of credentials embedded in a clone URL and returns the redacted
  form; nothing here re-derives an unredacted value.
* **``run_id`` is validated strictly** -- see :data:`RunId`. It arrives from the URL
  path of three routes, which makes it untrusted input.
* **Validation errors leak nothing.** :meth:`ApiErrorResponse.from_validation_error`
  strips pydantic's ``input``, ``url`` and ``ctx`` payloads, so a rejected body
  cannot be reflected back and a configuration value can never appear in an error.
  No traceback and no filesystem path is representable in the error envelope.
* **Request strings that become subprocess arguments are constrained** so they
  cannot masquerade as command-line options, and the one field that becomes a path
  component rejects absolute paths and parent-directory segments.

Layering
--------
The application's dependency direction is fixed at ``api -> services -> reporting
-> utils``, and nothing under ``app/`` may import from ``tests/``. This module sits
at the top of that chain and imports only the standard library, ``pydantic`` and
``app.reporting.thresholds``. It never imports ``app.api.routes`` (which imports
the blueprint this module's consumers decorate, and would close a cycle),
``app.web``, ``app.services``, ``tests`` or ``scripts``, and it never touches a
test-harness distribution: the deployed container installs ``requirements.txt``
alone, along the chain ``wsgi.py`` -> ``app/__init__.py`` -> the blueprints ->
``app/api/routes.py`` -> this module, so a single harness import here would stop
the container from starting.

Usage
-----
::

    >>> from app.api.schemas import RunRequest, load_request
    >>> load_request(RunRequest, None).tag_expression is None
    True
    >>> load_request(RunRequest, {}) == load_request(RunRequest, None)
    True
    >>> from app.api.schemas import PublisherThresholds
    >>> PublisherThresholds().model_dump()["failedStepsNumber"]
    -1
    >>> sorted(PublisherThresholds().model_dump())[0]
    'failedFeaturesNumber'
"""

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

from app.reporting.thresholds import (
    REPORT_FAILED_FEATURES_NUMBER,
    REPORT_FAILED_SCENARIOS_NUMBER,
    REPORT_FAILED_STEPS_NUMBER,
    REPORT_FILE_INCLUDE_PATTERN,
    REPORT_PENDING_STEPS_NUMBER,
    REPORT_SKIPPED_STEPS_NUMBER,
    REPORT_SORTING_METHOD,
    REPORT_UNDEFINED_STEPS_NUMBER,
)

__all__ = [
    "DISABLED_THREAD_COUNT",
    "ERROR_CODE_BAD_REQUEST",
    "FEATURE_KEYS",
    "FEATURE_KEY_ORDER",
    "HTTP_BAD_REQUEST",
    "MAX_ARGUMENT_LENGTH",
    "RESULT_KEYS",
    "RESULT_KEY_ORDER",
    "RUN_ID_LENGTH",
    "RUN_ID_PATTERN",
    "SCENARIO_KEYS",
    "SCENARIO_KEY_ORDER",
    "STEP_KEYS",
    "STEP_KEY_ORDER",
    "VALIDATION_ERROR_MESSAGE",
    "ApiErrorDetail",
    "ApiErrorResponse",
    "ApiRequestModel",
    "ApiResponseModel",
    "ArtifactLayout",
    "ArtifactStatus",
    "CloneAction",
    "ClonePolicy",
    "CloneRequest",
    "CloneResponse",
    "CommandArgument",
    "ConfigResponse",
    "CucumberReportStatus",
    "ExecutionPolicy",
    "NonNegativeCount",
    "PositiveSeconds",
    "PublisherSettings",
    "PublisherThresholds",
    "RelativeDirectory",
    "ReportArtifact",
    "ReportRequest",
    "ReportResponse",
    "RunId",
    "RunRequest",
    "RunResponse",
    "RunStatusResponse",
    "RunSummaryModel",
    "ShotCollectionModel",
    "ShotDirectoryState",
    "ShotFileModel",
    "ShotGroup",
    "ShotIndexResponse",
    "ShotPolicy",
    "StrictFlag",
    "TagExpression",
    "TestRunOutcome",
    "ThreadCountSetting",
    "WorkerAllocation",
    "is_valid_run_id",
    "load_request",
    "validate_run_id",
]


# =============================================================================
# The error envelope's vocabulary.
#
# `app/errors.py` registers the 404, 405 and 500 handlers at APPLICATION level --
# a blueprint does not own a URL space, so a 404 handler registered on a
# blueprint is never invoked for an unmatched URL -- and answers JSON for
# `/api/v1/*` paths and rendered HTML elsewhere. Its JSON body is a machine
# readable code, a human-readable message and the numeric status.
#
# The envelope below deliberately reuses that shape rather than introducing a
# competing one, so a client sees ONE error format whether it sent an invalid
# body, called an unmatched URL or hit an internal fault. The code is the
# snake_case rendering of the HTTP reason phrase, which is the same family as the
# handlers' own codes (`not_found`, `method_not_allowed`, `internal_server_error`).
# =============================================================================

HTTP_BAD_REQUEST: Final[int] = 400
"""Status returned when a request body fails validation."""

ERROR_CODE_BAD_REQUEST: Final[str] = "bad_request"
"""Machine-readable code paired with :data:`HTTP_BAD_REQUEST`."""

VALIDATION_ERROR_MESSAGE: Final[str] = "The request body is not valid."
"""Human-readable message for a rejected request body.

Deliberately generic. The per-field particulars travel in
:attr:`ApiErrorResponse.details`, which carries only pydantic's own sanitised
wording -- never the offending value, so nothing a caller sent is reflected back.
"""


# =============================================================================
# `run_id`: an opaque correlation identifier, and a security boundary.
#
# `run_id` reaches the application from the URL path of three routes
# (`GET /api/v1/runs/<run_id>`, `GET /api/v1/reports/<run_id>/cucumber.json` and
# `GET /api/v1/reports/<run_id>/rerun.txt`, with the screenshot index alongside
# them), which makes it untrusted input on every one of them. It is validated
# HERE, once, so all of them share a single authoritative definition instead of
# each route inventing its own check.
#
# IT IS A CORRELATION IDENTIFIER AND NEVER A PATH COMPONENT. The four report
# artifacts live at fixed literal locations owned by `app/utils/paths.py`, and
# the test-runner service never creates a per-run subdirectory beneath the
# artifact root: relocating the artifacts would break both those literals and the
# `fileIncludePattern: '**/*.json'` contract of `[Jenkins:L15]`. So a `run_id`
# ties a request to a run in logs and payloads, and that is all it does. It must
# never be joined into a path, and no code in this codebase does so.
#
# `app/services/test_runner_service.py` mints it as `uuid.uuid4().hex`, which is
# exactly 32 lowercase hexadecimal characters. The constraint below is therefore
# an equality test in all but name, and it is intentionally that tight: length
# bounds plus an anchored character class leave no room for a path separator, a
# parent-directory segment, a percent-encoded separator, a null byte, a newline,
# whitespace, an upper-case character or any other non-hex byte.
# =============================================================================

RUN_ID_LENGTH: Final[int] = 32
"""Length of a valid ``run_id``: the digest length of ``uuid.uuid4().hex``."""

RUN_ID_PATTERN: Final[str] = r"^[0-9a-f]{32}$"
"""Anchored pattern a valid ``run_id`` must match in full.

Lower case only, hexadecimal only, anchored at both ends. Paired with the length
bounds below it also rejects an otherwise-valid identifier carrying a trailing
newline, which an anchored pattern alone can admit under some regex dialects.
"""

_RUN_ID_REGEX: Final[re.Pattern[str]] = re.compile(RUN_ID_PATTERN)

_RUN_ID_REQUIREMENT: Final[str] = (
    f"run_id must be exactly {RUN_ID_LENGTH} lowercase hexadecimal characters"
)


def is_valid_run_id(value: object) -> bool:
    """Report whether ``value`` is a well-formed ``run_id``.

    Total and side-effect free: it accepts any object, answers ``True`` or
    ``False`` and never raises. A route that would rather answer ``404`` than
    ``400`` for a malformed identifier can branch on this instead of catching an
    exception.

    Args:
        value: Candidate identifier. Anything that is not a :class:`str` -- an
            ``int``, ``None``, a ``bytes`` object -- is answered ``False`` rather
            than coerced, because coercion is how an unexpected type becomes an
            unexpected string.

    Returns:
        ``True`` only for a string of exactly :data:`RUN_ID_LENGTH` lowercase
        hexadecimal characters. Every path separator, parent-directory segment,
        percent-encoded separator, null byte, control character and upper-case or
        non-hex character is rejected.

    Example:
        >>> import uuid
        >>> is_valid_run_id(uuid.uuid4().hex)
        True
        >>> is_valid_run_id("../etc/passwd")
        False
        >>> is_valid_run_id(None)
        False
    """
    if not isinstance(value, str):
        return False
    if len(value) != RUN_ID_LENGTH:
        return False
    return _RUN_ID_REGEX.fullmatch(value) is not None


def validate_run_id(value: str) -> str:
    """Return ``value`` unchanged when it is a well-formed ``run_id``.

    The enforcement point for the path parameter of the three routes that accept
    one, and the validator behind :data:`RunId`.

    Args:
        value: Candidate identifier, exactly as it arrived.

    Returns:
        The identifier unchanged. Nothing is stripped, lower-cased or otherwise
        normalised: a value that needs adjusting to become valid is not valid, and
        quietly repairing it would widen the boundary this function exists to hold.

    Raises:
        ValueError: If the identifier is malformed. The message states the
            requirement and **never quotes the supplied value**, so a rejected
            identifier cannot be reflected back to the caller through the error
            envelope.
    """
    if not is_valid_run_id(value):
        raise ValueError(_RUN_ID_REQUIREMENT)
    return value


RunId = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=RUN_ID_PATTERN,
        min_length=RUN_ID_LENGTH,
        max_length=RUN_ID_LENGTH,
        strip_whitespace=False,
        to_lower=False,
    ),
    AfterValidator(validate_run_id),
]
"""A validated ``run_id`` for use as a model field annotation.

Belt and braces on purpose: the string constraints are enforced by pydantic's own
core, and :func:`validate_run_id` re-checks the result with :mod:`re` afterwards.
The two are independent implementations of one rule, so a difference in regex
dialect between them cannot open a gap.

``strict=True`` is load-bearing rather than decorative. Without it pydantic's lax
mode decodes a ``bytes`` object into a ``str`` BEFORE the pattern is applied, so
``b"0" * 32`` would satisfy an identifier check that is meant to accept text only.
Nothing legitimate is refused by insisting on ``str``: JSON has no bytes type, and
:func:`uuid.uuid4` yields ``hex`` as a string.
"""


# =============================================================================
# Guards for request strings that become subprocess arguments.
#
# `app/services/clone_service.py` and `app/services/test_runner_service.py` run
# their subprocesses from ARGUMENT LISTS and never through a shell, so shell
# metacharacters are inert and there is no interpolation to escape. Two hazards
# survive that design and are closed here, at the point the value enters the
# application:
#
#   * ARGUMENT INJECTION. A value beginning with `-` is read by `git` or by
#     `pytest` as an option rather than as an operand, which turns a data field
#     into a flag. Every constrained field below therefore refuses a leading `-`.
#   * CONTROL CHARACTERS. A null byte cannot be passed to a system call at all
#     (Python raises), and a newline or other control character corrupts log
#     records and report output. Both are refused outright.
#
# EVERY CONSTRAINED TYPE BELOW IS STRICT, and that is a boundary decision rather
# than a stylistic one. pydantic's default lax mode silently coerces at exactly the
# places where a silent coercion is worst:
#
#   * `bytes` are decoded into `str`, so a bytes object could satisfy an
#     identifier pattern that a `str`-only check would have refused;
#   * `True` is read as the integer `1`, so a mistyped timeout could become one
#     second; and
#   * `"300"` is read as `300`, blurring the difference between a well-formed body
#     and a nearly-well-formed one.
#
# The worst case is the failure-tolerance switch: reading a loose value as `False`
# would silently ENABLE the build gating that the source deliberately never had,
# changing preserved behaviour on the strength of a coercion nobody asked for.
# Strict validation means an ill-typed field is reported, in the same spirit as
# refusing unknown keys instead of dropping them. JSON carries no bytes and no
# ambiguity about numbers, so nothing legitimate is lost.
#
# These guards apply to REQUEST fields only. A response field carries whatever
# configuration actually resolved, because an introspection endpoint that refused
# to serialise a mis-set value would answer `500` and hide the very
# misconfiguration the caller is looking for.
# =============================================================================

MAX_ARGUMENT_LENGTH: Final[int] = 2048
"""Upper bound on any request string that becomes a subprocess argument.

Generous enough for the longest plausible clone URL and far below any platform
argument limit, so an oversized body is rejected as data rather than surfacing
later as an operating-system error.
"""

_OPTION_PREFIX: Final[str] = "-"

_PARENT_SEGMENT: Final[str] = ".."

_PATH_SEPARATORS: Final[tuple[str, str]] = ("/", "\\")


def _ensure_no_control_characters(value: str) -> str:
    """Return ``value`` unchanged unless it contains a control character."""
    if any(character.isspace() and character != " " for character in value):
        raise ValueError("value must not contain line breaks, tabs or other whitespace controls")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError("value must not contain control characters")
    return value


def _ensure_argument_safe(value: str) -> str:
    """Validate a request string destined for a subprocess argument list.

    Args:
        value: The string exactly as it arrived.

    Returns:
        The string unchanged. Nothing is trimmed or rewritten: a preserved
        configuration value must survive a round trip byte for byte, and silently
        repairing input is how a boundary stops being one.

    Raises:
        ValueError: If the string is blank, carries a control character, or begins
            with ``-`` and could therefore be mistaken for a command-line option.
    """
    _ensure_no_control_characters(value)
    if not value.strip():
        raise ValueError("value must not be blank")
    if value.startswith(_OPTION_PREFIX):
        raise ValueError("value must not begin with '-', which a command reads as an option")
    return value


def _ensure_tag_expression_safe(value: str) -> str:
    """Validate a pytest mark expression bound for ``-m``.

    This is the one request string that MAY be empty, and the exception is
    load-bearing rather than lenient. The empty string is this repository's
    documented "no filter" selector: ``Makefile:L51`` defines
    ``NO_TAG_FILTER := -m ""`` and ``pytest.ini:L34`` records that the preserved
    default is overridden "on the command line with ``-m \"\"``". AAP AMB/defect
    D2 likewise resolves the zero-selecting default as "overridable by
    environment variable or configuration", so the HTTP surface has to be able
    to express the override or the documented mode becomes unreachable and the
    caller is left with no way to run the suite at all.

    A whitespace-only value is refused even though the empty string is accepted.
    ``""`` is the documented selector; ``"   "`` is indistinguishable from a
    typo, and refusing it surfaces the mistake instead of silently meaning
    "select everything" -- the same reasoning that makes unknown fields an error.

    Args:
        value: The mark expression exactly as it arrived, possibly empty.

    Returns:
        The value unchanged, never trimmed or rewritten.

    Raises:
        ValueError: If the value carries a control character, is blank without
            being empty, or begins with ``-`` and could be read as an option.
    """
    _ensure_no_control_characters(value)
    if value and not value.strip():
        raise ValueError(
            "value must be a mark expression or the empty string, "
            "which is the documented no-filter selector"
        )
    if value.startswith(_OPTION_PREFIX):
        raise ValueError("value must not begin with '-', which a command reads as an option")
    return value


def _ensure_relative_path_safe(value: str) -> str:
    """Validate a request string destined to become a directory name.

    The clone destination is the one request field a service joins onto a base
    directory, so it is held to a stricter standard than a plain argument: it must
    be relative and must not climb out of the directory it is joined to.

    Args:
        value: The candidate directory, exactly as it arrived.

    Returns:
        The value unchanged.

    Raises:
        ValueError: If the value is unsafe as a relative directory name -- blank,
            control-character bearing, option-like, absolute, drive-qualified, or
            carrying a parent-directory segment.
    """
    _ensure_argument_safe(value)
    if value.startswith(_PATH_SEPARATORS):
        raise ValueError("value must be a relative path, not an absolute one")
    if len(value) > 1 and value[1] == ":":
        raise ValueError("value must be a relative path, not a drive-qualified one")
    normalised = value.replace("\\", "/")
    if _PARENT_SEGMENT in normalised.split("/"):
        raise ValueError("value must not contain a '..' segment")
    return value


CommandArgument = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=MAX_ARGUMENT_LENGTH,
        strip_whitespace=False,
    ),
    AfterValidator(_ensure_argument_safe),
]
"""A request string safe to place in a subprocess argument list."""

RelativeDirectory = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=MAX_ARGUMENT_LENGTH,
        strip_whitespace=False,
    ),
    AfterValidator(_ensure_relative_path_safe),
]
"""A request string safe to use as a relative directory name."""

TagExpression = Annotated[
    str,
    StringConstraints(
        strict=True,
        max_length=MAX_ARGUMENT_LENGTH,
        strip_whitespace=False,
    ),
    AfterValidator(_ensure_tag_expression_safe),
]
"""A pytest mark expression, or the empty documented no-filter selector.

Deliberately carries no ``min_length``, unlike :data:`CommandArgument`. The
preserved default is ``LogOut`` ``[README.md:L87]``, which selects nothing at all
(defect D2), so ``""`` -- ``Makefile:L51``'s ``NO_TAG_FILTER := -m ""`` -- is the
only value that runs anything. Refusing it here would make the documented
override unreachable over HTTP.
"""

WorkerAllocation = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^(auto|logical|[0-9]{1,4})$", strip_whitespace=False),
]
"""A pytest-xdist worker allocation: ``logical``, ``auto`` or a count.

``logical`` is the port of Surefire's ``<parallel>methods</parallel>`` combined
with ``<useUnlimitedThreads>true</useUnlimitedThreads>`` ``[pom.xml:L22-L23]``, and
is the resolved default. A count of ``0`` requests serial execution, which is how
the mutually exclusive Gherkin terminal reporter is reached. Anything else is
refused before it can reach a command line.
"""

PositiveSeconds = Annotated[int, Field(strict=True, ge=1)]
"""A timeout in whole seconds. Zero, negative, boolean and string values are refused."""

NonNegativeCount = Annotated[int, Field(strict=True, ge=0)]
"""A tally that cannot be negative, and cannot be a boolean wearing an integer's coat."""

StrictFlag = Annotated[bool, Field(strict=True)]
"""A request boolean that must actually be a boolean.

Used for the two request switches that change preserved behaviour, where reading
``0`` or ``"no"`` as ``False`` would turn a mistyped body into a behavioural
change: the failure-tolerance switch of defect **D3** and the record of whether the
preceding stage failed.
"""


# =============================================================================
# The Cucumber JSON key sets. A published contract, frozen on purpose.
#
# `GET /api/v1/reports/<run_id>/cucumber.json` serves `cucumber.json` AS ITS OWN
# BYTES. It is not parsed into a model and re-serialised, and that is a deliberate
# decision rather than a shortcut: re-serialising is precisely how the emitted
# report would start to diverge from what the source toolchain produced, which is
# the divergence validation criterion V5 exists to catch. `app/reporting/
# cucumber_json.py` owns the schema LOGIC -- loading a document, telling absent
# from invalid from valid without ever raising, and deriving a summary -- and it
# holds the identical key sets for that purpose.
#
# The sets are nevertheless declared here as well, as immutable constants, because
# they are part of the API layer's published contract: the route and the
# integration suite assert the served document's shape against ONE definition, and
# that definition has to live where the HTTP contract lives. The two declarations
# are checked against each other, so the duplication cannot drift.
#
# The shape below was verified empirically, by generating the report and reading
# it back, and it is stable across serial and parallel runs.
# `tests/fixtures/expected_cucumber_report.json` freezes it as the golden fixture.
#
# TAGS CARRY NO LEADING `@`. Each entry of a `tags` array is an object whose
# `name` holds the bare tag -- `Login`, `UPGN-286`, `SalesManager` -- exactly as
# Cucumber-JVM itself emitted it. Nothing here re-adds the `@` that Gherkin uses
# in the feature file, and criterion V5 asserts the absence.
# =============================================================================

FEATURE_KEY_ORDER: Final[tuple[str, ...]] = (
    "description",
    "elements",
    "id",
    "keyword",
    "language",
    "line",
    "name",
    "tags",
    "uri",
)
"""The nine keys of a feature object, in the order the verified schema states."""

FEATURE_KEYS: Final[frozenset[str]] = frozenset(FEATURE_KEY_ORDER)
"""The nine feature-object keys. Immutable: it is a :class:`frozenset`."""

SCENARIO_KEY_ORDER: Final[tuple[str, ...]] = (
    "description",
    "id",
    "keyword",
    "line",
    "name",
    "steps",
    "tags",
    "type",
)
"""The eight keys of every member of a feature's ``elements`` array."""

SCENARIO_KEYS: Final[frozenset[str]] = frozenset(SCENARIO_KEY_ORDER)
"""The eight scenario-object keys.

``type`` is what distinguishes a scenario element from a background element, so
anything that interprets an element rather than merely validating it must branch
on that key first.
"""

STEP_KEY_ORDER: Final[tuple[str, ...]] = ("keyword", "line", "match", "name", "result")
"""The five keys of every member of a scenario's ``steps`` array."""

STEP_KEYS: Final[frozenset[str]] = frozenset(STEP_KEY_ORDER)
"""The five step-object keys."""

RESULT_KEY_ORDER: Final[tuple[str, ...]] = ("status", "duration")
"""The two keys a step's ``result`` sub-object carries, in schema order.

``duration`` counts nanoseconds, matching the source toolchain's own unit. A
failing step's result additionally carries ``error_message``, which is why a
consumer checks for the presence of these two rather than for an exact key set.
"""

RESULT_KEYS: Final[frozenset[str]] = frozenset(RESULT_KEY_ORDER)
"""The two required ``result`` keys."""


# =============================================================================
# Closed vocabularies.
#
# Every enumeration below is a `StrEnum`, so a member serialises straight into a
# JSON payload with no conversion table and no custom encoder, and comparing one
# against a plain string works as written. Each member's VALUE mirrors the
# vocabulary its owning module already publishes -- the API layer renames nothing,
# so an HTTP payload and the internal result it was built from cannot drift apart.
# =============================================================================


class TestRunOutcome(StrEnum):
    """How a test run ended, named rather than merely numbered.

    Six members, one for each exit code pytest can return, because two of those
    codes mean "succeeded" in ways a bare boolean cannot express. THE MAPPING FROM
    EXIT CODE TO MEMBER IS NOT IMPLEMENTED HERE: the non-gating exit-code policy
    belongs to ``app/services/test_runner_service.py``, which decides the outcome
    and the accompanying ``succeeded`` flag and passes both in. This enumeration
    supplies the vocabulary and the documentation, and deliberately offers no
    ``from_exit_code`` helper that would quietly duplicate that policy.

    The two surprising members are the important ones, and both are successes:

    * :attr:`NO_SCENARIOS_SELECTED` -- pytest exit code ``5``. The preserved
      default tag expression selects nothing, so the documented invocation runs no
      scenario at all (defect **D2**). The source pipeline went green in exactly
      this situation, so the port must too; treating exit ``5`` as an error would
      make the ported system fail where the original succeeded.
    * :attr:`FAILURES_IGNORED` -- pytest exit code ``1``. Tests ran and some
      failed, and the run is still a success because
      ``<testFailureIgnore>true</testFailureIgnore>`` ``[pom.xml:L25]`` and six
      ``-1`` publisher thresholds ``[Jenkins:L15]`` mean failures never gate
      (defect **D3**).
    """

    PASSED = "passed"
    """pytest exit ``0``: scenarios were selected and every one passed."""

    FAILURES_IGNORED = "failures_ignored"
    """pytest exit ``1``: scenarios failed, and the run still succeeded (D3)."""

    NO_SCENARIOS_SELECTED = "no_scenarios_selected"
    """pytest exit ``5``: everything was deselected, and that is success (D2)."""

    INTERRUPTED = "interrupted"
    """pytest exit ``2``: the session was interrupted. A hard failure."""

    INTERNAL_ERROR = "internal_error"
    """pytest exit ``3``: an internal error occurred. A hard failure."""

    USAGE_ERROR = "usage_error"
    """pytest exit ``4``: the command line was wrong. A hard failure."""


class CloneAction(StrEnum):
    """What the clone stage actually did.

    The source pipeline's ``git '<url>'`` step ``[Jenkins:L2-L4]`` was not a bare
    clone: it also cleaned the workspace and re-used an existing checkout, so
    running it twice was normal and never an error. The port therefore has to
    report *which* of those happened, which a boolean could not do.
    """

    CLONED = "cloned"
    """The destination was empty and a fresh clone was made."""

    UPDATED = "updated"
    """The destination already held the repository, which was fetched and reset."""

    FAILED = "failed"
    """Neither could be completed. Paired with ``succeeded`` false and a detail."""


class ArtifactStatus(StrEnum):
    """Whether one report artifact can be served, and if not, why not.

    Six outcomes rather than a boolean, because a caller can only be answered
    honestly if "no run has happened yet" is distinguishable from "a run happened
    and wrote nothing" and from "something is in the way". The values mirror the
    vocabulary ``app/reporting/html_report.py`` publishes for the same question.
    Only :attr:`AVAILABLE` means the bytes can be sent.

    An absent artifact is the ORDINARY state, not a fault: the artifact root is
    ephemeral and is wiped at the start of every run, and the preserved default
    invocation selects no scenario, so a default run writes no report at all.
    """

    AVAILABLE = "available"
    """A non-empty regular file, or a populated directory, is present."""

    EMPTY = "empty"
    """Present but empty, so there is nothing to serve."""

    MISSING = "missing"
    """Nothing exists at the path: the artifact has not been generated yet."""

    NOT_A_FILE = "not_a_file"
    """Something that is not the expected kind of entry is in the way."""

    INACCESSIBLE = "inaccessible"
    """The path exists in some form but could not be inspected."""

    OUTSIDE_ROOT = "outside_root"
    """The path resolves outside the artifact root and is refused on principle."""


class CucumberReportStatus(StrEnum):
    """The state of ``cucumber.json`` as ``app/reporting/cucumber_json.py`` reports it.

    That module distinguishes these three without ever raising, and the values
    below are its own. Absence and invalidity are answers, not exceptions.
    """

    ABSENT = "absent"
    """No report document exists yet."""

    INVALID = "invalid"
    """A document exists but does not match the verified schema."""

    VALID = "valid"
    """A document exists and matches the verified schema."""


class ShotGroup(StrEnum):
    """The two artifact groups promised by ``[README.md:L42-L43]``.

    The prose is quoted in full because the difference between the two groups is
    the reason they are never merged: the project "generate ``screen shots`` for
    your tests if you enable it and also generate ``error shots`` for your failed
    test cases as well". One is opt-in, the other is failure-driven.

    THE NAMING ASYMMETRY IS DELIBERATE AND PRESERVED: the screen-shot directory is
    one word with no hyphen, the error-shot directory is hyphenated. Both values
    match the directory names ``app/utils/paths.py`` owns and the group keys
    ``app/reporting/screenshots.py`` indexes under, and neither is regularised
    into the other's shape.
    """

    SCREEN_SHOTS = "screenshots"
    """One word, no hyphen. Opt-in: produced for a test "if you enable it"."""

    ERROR_SHOTS = "error-shots"
    """Hyphenated. Failure-driven: produced "for your failed test cases"."""


class ShotDirectoryState(StrEnum):
    """Outcome of indexing one shot group's directory.

    Four outcomes and not one of them is an exception, mirroring the vocabulary
    ``app/reporting/screenshots.py`` publishes. A fresh checkout has no artifact
    tree at all, so absence is the ordinary case rather than a fault, and an empty
    index is a successful response.
    """

    ABSENT = "absent"
    """The directory does not exist: nothing has been generated yet."""

    EMPTY = "empty"
    """The directory exists and holds no indexable artifact."""

    AVAILABLE = "available"
    """The directory exists and holds at least one indexable artifact."""

    UNREADABLE = "unreadable"
    """The path exists but could not be listed, or is not a directory at all."""


# =============================================================================
# The two base models, and the configuration every model inherits.
#
# `frozen=True`            A validated payload is a snapshot. A view function, a
#                          service or a log record can share one without any of
#                          them being able to edit it, and an accidental
#                          assignment is reported instead of silently taking
#                          effect.
# `extra="forbid"`         An unrecognised key is an ERROR, never ignored. On a
#                          request that is the whole point: a mistyped override
#                          would otherwise be dropped in silence and the run would
#                          proceed with a default the caller believed it had
#                          replaced. Given how many of this system's defaults are
#                          preserved defects, that silence would be actively
#                          misleading.
# `validate_by_alias` and  Both spellings are accepted on the way in, so a payload
# `validate_by_name`       may use the publisher's camelCase vocabulary or the
#                          Python field name, and internal code never has to spell
#                          a field the wire way.
# `serialize_by_alias`     Aliases are used on the way OUT BY DEFAULT, so a bare
#                          `model_dump()` already emits the publisher's own key
#                          spellings. Requiring `by_alias=True` at every call site
#                          would make snake_cased threshold keys one forgotten
#                          argument away, and those key names are a parity
#                          contract asserted by validation criterion V4.
# `str_strip_whitespace`   Left at its default, OFF. Trimming is a silent rewrite,
#                          and a value that needs trimming to be valid is not
#                          valid.
# =============================================================================


class ApiRequestModel(BaseModel):
    """Base class for every request body the ``/api/v1`` blueprint accepts.

    Subclasses declare **only optional fields**. An unset field is not a missing
    value: it is the request declining to override, which lets the setting resolve
    through the precedence chain to the configured or preserved source default. A
    required field would sever that chain, so there are none.

    Every subclass therefore validates an empty body, and :func:`load_request`
    extends that to a JSON ``null`` body and to no body at all.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


class ApiResponseModel(BaseModel):
    """Base class for every response body the ``/api/v1`` blueprint returns.

    Instances are immutable snapshots built from the frozen results the service and
    reporting layers return. Every field is a JSON primitive, a nested model, a
    :class:`~enum.StrEnum` member or a tuple of those, so ``model_dump()`` yields
    something :func:`json.dumps` accepts as it stands -- no path object, no
    ``datetime``, no exception and no custom encoder anywhere. Timestamps travel as
    ISO 8601 strings, exactly as the reporting layer already renders them.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


def load_request[RequestModelT: ApiRequestModel](
    model: type[RequestModelT], payload: object | None = None
) -> RequestModelT:
    """Validate a decoded JSON request body, treating an absent body as ``{}``.

    Flask hands back ``None`` for a request with no body and for one whose body is
    the JSON literal ``null``. Both mean the same thing here -- "override nothing"
    -- and so does an empty object, so all three are funnelled through one code
    path and produce an all-defaults instance. That equivalence is what makes a
    bare ``POST`` reproduce the source system's behaviour exactly, preserved
    defects and all.

    Args:
        model: The request model class to validate against.
        payload: The decoded JSON body. ``None`` -- an absent or ``null`` body --
            is treated as an empty object. Anything that is not a JSON object is
            rejected by pydantic with an ordinary validation error, so a stray
            array or string is reported rather than silently ignored.

    Returns:
        A validated, frozen instance of ``model``.

    Raises:
        pydantic.ValidationError: If the body is not a JSON object, carries an
            unrecognised key, or carries a value that fails a field's constraint.
            :meth:`ApiErrorResponse.from_validation_error` turns that into the
            wire envelope.

    Example:
        >>> load_request(CloneRequest, None).url is None
        True
        >>> load_request(CloneRequest, {"branch": "main"}).branch
        'main'
    """
    return model.model_validate({} if payload is None else payload)


# =============================================================================
# Request bodies for the three POST endpoints.
# =============================================================================


class CloneRequest(ApiRequestModel):
    """Body of ``POST /api/v1/clone`` -- the port of stage ``'Clone code'``.

    The source stage was a single statement, ``git '<url>'`` ``[Jenkins:L3]``, with
    every parameter fixed in the pipeline script. Here each of those parameters may
    be overridden per request, and any left unset resolves through configuration to
    the preserved source default.
    """

    url: CommandArgument | None = None
    """Repository to clone. Unset resolves to the configured clone URL.

    DEFECT **D7**, PRESERVED. The source names two different repositories: the
    pipeline clones ``https://github.com/BalamiRR/Upgenix-QA.git`` ``[Jenkins:L3]``
    while the README instructs cloning
    ``https://github.com/BalamiRR/Testinium-QA.git`` ``[README.md:L59]``. Executable
    configuration outranks prose and the Jira key prefix ``UPGN`` agrees with the
    pipeline, so the pipeline URL is the runtime default and the README URL is
    carried alongside it as :attr:`ClonePolicy.documented_url`. The discrepancy is
    documented, never silently unified; ``docs/migration-parity.md`` records it.

    Typed as a plain constrained string rather than as a URL model on purpose. A
    URL type normalises what it parses -- it may add a trailing slash or re-case a
    host -- and normalising a preserved configuration value is exactly what Rule T1
    forbids; it would also reject the scp-style and ``git+ssh`` forms git accepts.
    The value is validated for safety and otherwise passed through byte for byte.

    A URL an operator configures may embed credentials.
    ``app/services/clone_service.py`` owns redaction and returns an
    already-redacted URL, so no response field here ever carries a secret.
    """

    branch: CommandArgument | None = None
    """Branch to check out. Unset resolves to the configured branch."""

    directory: RelativeDirectory | None = None
    """Destination directory, relative to the workspace root.

    The one request field a service joins onto a base directory, so it is held to
    the stricter path rule: relative only, no drive qualifier, and no ``..``
    segment. Raw user input is never joined into a path anywhere in this codebase,
    and this constraint is why that stays true even when a caller supplies the
    name.
    """

    timeout_seconds: PositiveSeconds | None = None
    """Timeout for the clone. Unset resolves to the configured timeout.

    The source pipeline step had no explicit timeout; the port always sets one,
    because a subprocess that can hang forever is not production-ready.
    """


class RunRequest(ApiRequestModel):
    """Body of ``POST /api/v1/runs`` -- the port of stage ``'Run tests'``.

    The source stage dispatched on platform and ran ``mvn clean test``
    ``[Jenkins:L6-L11]``, taking its execution semantics from the Surefire
    configuration ``[pom.xml:L21-L29]``. Those semantics are preserved and are
    overridable per request.
    """

    tag_expression: TagExpression | None = None
    """Tag expression selecting scenarios. Unset resolves to the configured value.

    DEFECT **D2**, PRESERVED. The configured default is the port of
    ``tags = "@LogOut"`` ``[README.md:L87]`` -- pytest-bdd turns a Gherkin tag into
    a marker and strips the ``@``, so the selector is ``LogOut``. No scenario in the
    suite carries that tag; the only tags present are ``Login``, ``UPGN-286``,
    ``UPGN-287``, ``UPGN-288``, ``SalesManager`` and ``PosManager``. Run exactly as
    documented, the suite therefore selects nothing, pytest exits ``5``, and THAT IS
    A SUCCESS -- see :attr:`TestRunOutcome.NO_SCENARIOS_SELECTED`. Supplying a
    different expression here is the documented way to opt into a run that
    actually selects scenarios; the default is never quietly corrected.

    Typed :data:`TagExpression` rather than :data:`CommandArgument` so the EMPTY
    string is accepted: ``Makefile:L51``'s ``NO_TAG_FILTER := -m ""`` and
    ``pytest.ini:L34`` both document ``-m ""`` as the way to clear the filter, and
    because the preserved default selects nothing, clearing it is the only way to
    run anything at all. Rejecting ``""`` here would leave the documented override
    with no HTTP representation.
    """

    workers: WorkerAllocation | None = None
    """Worker allocation. Unset resolves to the configured value, ``logical``.

    ``logical`` is the port of ``<parallel>methods</parallel>`` with
    ``<useUnlimitedThreads>true</useUnlimitedThreads>`` ``[pom.xml:L22-L23]``. Pass
    ``0`` for serial execution, which is the only mode the Gherkin terminal
    reporter can run in -- it is mutually exclusive with parallel workers.
    """

    ignore_failures: StrictFlag | None = None
    """Whether test failures are tolerated. Unset resolves to the configured value.

    DEFECT **D3**, PRESERVED. The configured default is ``True``, the port of
    ``<testFailureIgnore>true</testFailureIgnore>`` ``[pom.xml:L25]``, which the
    six ``-1`` publisher thresholds of ``[Jenkins:L15]`` reinforce: the source
    build could not fail on test results. Setting this to ``False`` per request is
    the explicit, documented way to opt into gating; build-failure gating stays
    switched off by default, which is what preserves the defect.
    """

    timeout_seconds: PositiveSeconds | None = None
    """Timeout for the whole run. Unset resolves to the configured timeout."""


class ReportRequest(ApiRequestModel):
    """Body of ``POST /api/v1/reports`` -- the port of stage ``'Generate report'``.

    The source stage was the single publisher statement of ``[Jenkins:L15]``, whose
    parameters are preserved constants owned by ``app/reporting/thresholds.py``.
    They are deliberately NOT overridable here: they are the parity contract that
    validation criterion V4 asserts, so this body carries only the two pieces of
    context the stage needs.
    """

    run_id: RunId | None = None
    """Correlation identifier of the run this report belongs to.

    Optional because the source stage ran unconditionally with no knowledge of what
    preceded it. Purely a correlation value: the four artifacts live at fixed
    literal paths and no per-run directory exists, so this is never used to locate
    anything on disk.
    """

    preceding_stage_failed: StrictFlag | None = None
    """Whether the test stage that ran before this one failed.

    DEFECT **D3**, PRESERVED. Reported for the record, and it changes nothing:
    ``'Generate report'`` ``[Jenkins:L14]`` followed ``'Run tests'`` unconditionally
    in the source pipeline, so the port must publish after a failed run too.
    Omitting that would break the non-gating behaviour in the one case where it
    matters most. This flag must never become a condition on generation.
    """


# =============================================================================
# The disabled tuning value, and the one constant this module owns outright.
#
# The source Surefire configuration reads:
#
#     <parallel>methods</parallel>                          [pom.xml:L22]
#     <useUnlimitedThreads>true</useUnlimitedThreads>        [pom.xml:L23]
#     <!--                    <threadCount>4</threadCount>-->  [pom.xml:L24]
#
# The third line is COMMENTED OUT in the source, and it stays that way: the value
# is preserved as a documented, disabled tuning default rather than enabled. The
# ACTIVE parallelism setting is the worker allocation `logical`, which is what
# unlimited method-level threads map onto.
#
# It is declared here because nothing else owns it. `app/reporting/thresholds.py`
# holds the publisher constants and disclaims the rest; `app/config.py` resolves
# settings that have a configuration key, and this one deliberately has none --
# neither `.env.example` nor `configuration.properties.example` offers a
# thread-count key, precisely because the value is inactive. Turning it on is an
# owner decision recorded in `docs/migration-parity.md`, not a default.
# =============================================================================

DISABLED_THREAD_COUNT: Final[int] = 4
"""The thread count the source kept commented out at ``[pom.xml:L24]``.

Carried across verbatim and reported as inactive by :class:`ThreadCountSetting`.
It is documentation with a value, not a tuning knob that is on.
"""


class ThreadCountSetting(ApiResponseModel):
    """A tuning value that is present in the configuration record but not in force.

    The source's commented-out ``<threadCount>4</threadCount>`` ``[pom.xml:L24]`` has
    to be reportable without being applied, which a bare integer cannot express and
    a bare ``None`` would erase. Two fields say it exactly: the value, and whether
    it is active.

    Example:
        >>> ThreadCountSetting().model_dump()
        {'value': 4, 'enabled': False}
    """

    value: NonNegativeCount = DISABLED_THREAD_COUNT
    """The recorded thread count, ``4``, exactly as the source commented it out."""

    enabled: bool = False
    """Whether the count is in force. ``False`` preserves the commented-out state."""


class ClonePolicy(ApiResponseModel):
    """Resolved settings of the ``'Clone code'`` stage ``[Jenkins:L2-L4]``.

    Every field is required and has no default here: ``app/config.py`` owns these
    values and resolves them through the precedence chain, so the route supplies
    what was actually resolved. A default in this class would be a second, silently
    diverging copy of a value this module does not own -- the source citations below
    are documentation, deliberately not declarations.
    """

    url: str = Field(...)
    """The clone URL in force. Source default: the ``Upgenix-QA`` URL ``[Jenkins:L3]``.

    Carries the REDACTED form. ``app/services/clone_service.py`` owns redaction of
    any credentials an operator embedded in the URL and returns the redacted value;
    nothing reconstructs the original, so this field cannot leak a secret.
    """

    documented_url: str | None = Field(...)
    """The other URL the source names: ``Testinium-QA`` ``[README.md:L59]``.

    DEFECT **D7**, PRESERVED. Reported alongside :attr:`url` rather than reconciled
    with it, because both strings are information and erasing either would lose it.
    Required but nullable, so a deployment that clears the setting says so
    explicitly instead of the discrepancy quietly vanishing from the record.
    """

    branch: str | None = Field(...)
    """The branch in force, or ``None`` when the remote default is used."""

    directory: str = Field(...)
    """The destination directory, relative to the workspace root."""

    timeout_seconds: NonNegativeCount = Field(...)
    """The clone timeout in whole seconds."""


class ExecutionPolicy(ApiResponseModel):
    """Resolved settings of the ``'Run tests'`` stage ``[Jenkins:L6-L11]``.

    The Python rendering of the Surefire configuration ``[pom.xml:L21-L29]``. As in
    :class:`ClonePolicy`, the values are owned by ``app/config.py`` and supplied by
    the route; only the disabled thread count defaults, because only it is owned
    here.
    """

    tag_expression: str = Field(...)
    """The tag expression in force. Source default: ``LogOut`` ``[README.md:L87]``.

    DEFECT **D2**, PRESERVED: that selector matches no scenario in the suite, so
    the documented invocation runs nothing and succeeds. Typed as a plain ``str``
    rather than as the constrained request type on purpose -- an introspection
    endpoint must be able to report the value an operator actually set, even a
    malformed one. Refusing to serialise it would answer ``500`` and hide exactly
    the misconfiguration the caller is looking for.
    """

    ignore_test_failures: bool = Field(...)
    """Whether failures are tolerated. Source default: ``True`` ``[pom.xml:L25]``.

    DEFECT **D3**, PRESERVED. Reported, never acted on here.
    """

    workers: str = Field(...)
    """The worker allocation in force. Source default: ``logical``.

    The port of ``<parallel>methods</parallel>`` plus
    ``<useUnlimitedThreads>true</useUnlimitedThreads>`` ``[pom.xml:L22-L23]``, and the
    ACTIVE parallelism setting. Reported as a plain string for the same reason as
    :attr:`tag_expression`.
    """

    thread_count: ThreadCountSetting = Field(default_factory=ThreadCountSetting)
    """The commented-out tuning value ``[pom.xml:L24]``, reported as inactive.

    Defaults to the recorded, disabled state, which is the only state the source
    ever had.
    """

    timeout_seconds: NonNegativeCount = Field(...)
    """The run timeout in whole seconds."""


class PublisherThresholds(ApiResponseModel):
    """The six report-publication thresholds of ``[Jenkins:L15]``.

    THE KEY NAMES ARE THE PUBLISHER'S OWN AND SURVIVE VERBATIM. They are the Groovy
    call's named parameters, they are camelCase, and they are declared below in
    their source order, so ``model_dump()`` emits exactly the six spellings the
    source pipeline used, in exactly that order. Snake-casing them, reordering them
    or dropping one would break the parity that validation criterion V4 asserts.
    Because :data:`ApiResponseModel.model_config` serialises by alias, no call site
    has to remember an argument to get that right.

    THE VALUES ARE IMPORTED, NEVER RESTATED. Each default comes from
    ``app/reporting/thresholds.py``, the single module that declares them, so this
    class cannot disagree with the constants the report service and the CI
    definition use. A literal ``-1`` written here would be a second declaration and
    a divergence waiting to happen.

    DEFECT **D3**, PRESERVED: the publisher reads ``-1`` as "no threshold", and a
    limit of ``-1`` can never be exceeded, so no count of failures, pending, skipped
    or undefined steps can ever mark the build unstable. Nothing in this class
    evaluates a threshold or derives a verdict from one.

    Example:
        >>> list(PublisherThresholds().model_dump())[0]
        'failedFeaturesNumber'
        >>> set(PublisherThresholds().model_dump().values())
        {-1}
    """

    failed_features_number: int = Field(
        default=REPORT_FAILED_FEATURES_NUMBER, alias="failedFeaturesNumber"
    )
    """Limit on failed features. ``-1`` means "no threshold" ``[Jenkins:L15]``."""

    failed_scenarios_number: int = Field(
        default=REPORT_FAILED_SCENARIOS_NUMBER, alias="failedScenariosNumber"
    )
    """Limit on failed scenarios. ``-1`` means "no threshold" ``[Jenkins:L15]``."""

    failed_steps_number: int = Field(default=REPORT_FAILED_STEPS_NUMBER, alias="failedStepsNumber")
    """Limit on failed steps. ``-1`` means "no threshold" ``[Jenkins:L15]``."""

    pending_steps_number: int = Field(
        default=REPORT_PENDING_STEPS_NUMBER, alias="pendingStepsNumber"
    )
    """Limit on pending steps. ``-1`` means "no threshold" ``[Jenkins:L15]``."""

    skipped_steps_number: int = Field(
        default=REPORT_SKIPPED_STEPS_NUMBER, alias="skippedStepsNumber"
    )
    """Limit on skipped steps. ``-1`` means "no threshold" ``[Jenkins:L15]``."""

    undefined_steps_number: int = Field(
        default=REPORT_UNDEFINED_STEPS_NUMBER, alias="undefinedStepsNumber"
    )
    """Limit on undefined steps. ``-1`` means "no threshold" ``[Jenkins:L15]``."""


class PublisherSettings(ApiResponseModel):
    """Every parameter of the report-publication call ``[Jenkins:L15]``.

    The source statement passed its eight parameters by name, alphabetically, so
    the include pattern and the sort order sat interleaved among the thresholds.
    The port keeps the six thresholds together in a nested object -- they are one
    concept and one imported mapping -- and carries the other two beside it, each
    still spelled the publisher's way.
    """

    thresholds: PublisherThresholds = Field(default_factory=PublisherThresholds)
    """The six thresholds, each ``-1``, in their ``[Jenkins:L15]`` order."""

    file_include_pattern: str = Field(
        default=REPORT_FILE_INCLUDE_PATTERN, alias="fileIncludePattern"
    )
    """The publisher's report-discovery pattern ``[Jenkins:L15]``. DATA ONLY.

    Imported from ``app/reporting/thresholds.py`` and echoed back verbatim. This is
    the one leading-wildcard string the migration lets survive into the Python
    tree, and it survives strictly as a value: it is never expanded against the
    filesystem, never compiled as a regular expression, never normalised, never
    split and never rewritten. Matching files is the CI publisher's job, which
    still works unedited because the port writes its reports under the artifact
    root whose name ``app/utils/paths.py`` preserves.
    """

    sorting_method: str = Field(default=REPORT_SORTING_METHOD, alias="sortingMethod")
    """The order features are sorted in before rendering ``[Jenkins:L15]``.

    Imported from ``app/reporting/thresholds.py`` in the publisher's own upper case.
    Applying it -- sorting features by name -- is
    ``app/services/report_service.py``'s behaviour, not this model's; the model
    reports the requested order and nothing more.
    """


class ArtifactLayout(ApiResponseModel):
    """Where each report artifact lives, as POSIX strings supplied by the caller.

    ``app/utils/paths.py`` owns the artifact root and every path beneath it, so
    every field here is required and NO PATH LITERAL IS DECLARED IN THIS MODULE.
    That is not fastidiousness: the artifact root keeps the source build tool's
    directory name deliberately, which is the entire reason the publisher's
    ``fileIncludePattern`` needs no edit, and a second spelling of it anywhere would
    put that contract at risk. Nothing here can rename the root, because nothing
    here names it.

    The tree is ephemeral: it is wiped at the start of every run, so a path being
    present in this layout says nothing about a file existing at it. That question
    is :class:`ArtifactStatus`'s to answer.
    """

    root: str = Field(...)
    """The artifact root, exactly as the path module spells it."""

    cucumber_json: str = Field(...)
    """The Cucumber JSON report, ported from the ``json:`` plugin ``[README.md:L79]``."""

    cucumber_html: str = Field(...)
    """The HTML report, ported from the ``html:`` plugin ``[README.md:L78]``."""

    rerun_txt: str = Field(...)
    """The rerun manifest, ported from the ``rerun:`` plugin ``[README.md:L80]``."""

    pretty_reports_dir: str = Field(...)
    """The PrettyReports directory, ported from the fourth plugin ``[README.md:L81]``."""

    screenshots_dir: str = Field(...)
    """Where opt-in screen shots are written ``[README.md:L42]``."""

    error_shots_dir: str = Field(...)
    """Where failure-driven error shots are written ``[README.md:L43]``."""

    surefire_reports_dir: str = Field(...)
    """The report directory whose name is retained for report-consumer parity."""


class ShotPolicy(ApiResponseModel):
    """Whether each shot group is being produced ``[README.md:L42-L43]``.

    Two switches rather than one, because the source describes two different
    triggers: screen shots happen "if you enable it" and error shots happen "for
    your failed test cases". Owned by ``app/config.py``, so both are required.
    """

    screenshots_enabled: bool = Field(...)
    """Whether screen shots are captured. Opt-in; the source default is off."""

    error_shots_enabled: bool = Field(...)
    """Whether error shots are captured on failure. The source default is on."""


class ConfigResponse(ApiResponseModel):
    """Body of ``GET /api/v1/config``: the resolved configuration, for inspection.

    This is the endpoint validation criterion V11 grades, and through it the
    criterion V4 constants: all six thresholds resolve to ``-1``, the sort order to
    ``ALPHABETICAL``, the include pattern to the literal ``**/*.json``, failure
    tolerance to true, and the tag expression to the preserved ``LogOut`` selector.
    Every one of those values reaches this model from the module that owns it, so
    the endpoint reports the configuration in force rather than a copy of it.

    NO SECRET IS REPRESENTABLE HERE. There is no field for a secret key, a
    password, a token, an API key or any other credential, and none may be added:
    the application reads its signing key from the environment with no usable
    default precisely so that it is never committed and never exposed, and an
    introspection endpoint is the last place it should surface. The only URL this
    response carries is already redacted by the clone service. Harness-side browser
    and driver settings are likewise absent: they belong to the test harness, and
    this endpoint describes the deployed service.
    """

    environment: str = Field(...)
    """Name of the configuration profile in force, such as ``production``."""

    clone: ClonePolicy = Field(...)
    """Settings of stage ``'Clone code'`` ``[Jenkins:L2-L4]``."""

    execution: ExecutionPolicy = Field(...)
    """Settings of stage ``'Run tests'`` ``[Jenkins:L6-L11]``."""

    publisher: PublisherSettings = Field(default_factory=PublisherSettings)
    """Settings of stage ``'Generate report'`` ``[Jenkins:L15]``.

    Defaults to the imported constants, which are the only values the source ever
    had, so the parity block is correct even when constructed with no arguments.
    """

    artifacts: ArtifactLayout = Field(...)
    """Where the report artifacts live, as owned by ``app/utils/paths.py``."""

    shots: ShotPolicy = Field(...)
    """Whether screen shots and error shots are being produced ``[README.md:L42-L43]``."""


# =============================================================================
# Stage results.
#
# Each model below mirrors the frozen result its service returns, field for field
# and name for name, so the HTTP payload and the internal value cannot drift into
# two vocabularies for one concept.
#
# Two outcomes that look like failures are FIRST-CLASS SUCCESSES here, and every
# model is shaped so they can be said plainly rather than inferred:
#
#   * zero scenarios selected (defect D2), and
#   * scenarios failed and the run still succeeded (defect D3).
#
# Consequently NOTHING BELOW GATES. No field asserts that `succeeded` must be
# false when a failure count is positive, no validator derives a verdict from a
# tally, and no "unstable" or "quality gate" concept exists anywhere in this
# module. The source build could not fail on test results and neither can this one.
# =============================================================================


class RunSummaryModel(ApiResponseModel):
    """Summary of one report document, as derived by the reporting layer.

    READ-ONLY AND DERIVED. Every count here is computed by
    ``app/reporting/cucumber_json.py`` from ``cucumber.json``; this model transports
    those numbers and computes none of them. The field names and the two derived
    flags mirror that module's summary exactly, so a route can hand the summary
    straight over::

        RunSummaryModel.model_validate(summary.as_dict())

    A summary with ``scenario_count == 0`` is NOT a failure -- it is the ordinary
    outcome of the documented invocation (defect **D2**) -- and a summary with a
    positive ``failed_scenario_count`` does not make the run a failure either
    (defect **D3**). Consumers key off :attr:`has_failures`, never off
    ``status != "passed"``.
    """

    status: str = Field(...)
    """Aggregated status across every scenario, in the report's own vocabulary."""

    feature_count: NonNegativeCount = 0
    """Features in the document, after same-``uri`` merging."""

    scenario_count: NonNegativeCount = 0
    """Scenario elements, backgrounds excluded. ``0`` is a success (D2)."""

    background_count: NonNegativeCount = 0
    """``Background`` elements, when the producer emits them separately."""

    step_count: NonNegativeCount = 0
    """Steps across every element."""

    failed_scenario_count: NonNegativeCount = 0
    """Scenarios with at least one failed step. A positive value still succeeds (D3)."""

    duration_ns: NonNegativeCount = 0
    """Summed step duration in nanoseconds, the source toolchain's own unit."""

    scenario_status_counts: Mapping[str, int] = Field(default_factory=dict)
    """Scenario tally by status, keys sorted by the reporting layer."""

    step_status_counts: Mapping[str, int] = Field(default_factory=dict)
    """Step tally by status, keys sorted by the reporting layer."""

    tags: tuple[str, ...] = ()
    """Every distinct tag seen at any level, sorted, WITH NO LEADING ``@``.

    The report emits bare tag names -- ``Login``, ``UPGN-286``, ``SalesManager`` --
    exactly as the source toolchain did, and nothing here re-adds the ``@``.
    """

    has_failures: bool = False
    """Whether at least one scenario failed. Derived by the reporting layer."""

    is_empty: bool = True
    """Whether the document contains no scenario at all. Derived, and a success."""


class ReportArtifact(ApiResponseModel):
    """One report artifact, described uniformly enough to be listed beside the others.

    The field set is the common ground across the four adapters -- the JSON report,
    the HTML report, the rerun manifest and the PrettyReports directory -- plus the
    name that says which one this is. Adapter-specific extras, such as whether the
    HTML report is self-contained, are deliberately left out: an artifact list is
    only useful if every entry answers the same questions.

    An entry whose status is not :attr:`ArtifactStatus.AVAILABLE` is ordinary, not
    an error. The artifact tree is wiped at the start of every run, and the
    preserved default invocation writes no report at all.
    """

    name: str = Field(...)
    """Which artifact this is, in the vocabulary the reporting layer uses."""

    path: str = Field(...)
    """Where it lives, as a POSIX string supplied by ``app/utils/paths.py``."""

    status: ArtifactStatus = Field(...)
    """Whether it can be served, and if not, why not."""

    exists: bool = False
    """Whether anything is present at the path."""

    available: bool = False
    """Whether the artifact can be served as-is."""

    size_bytes: NonNegativeCount = 0
    """Size in bytes, or ``0`` when nothing is there."""

    modified_at: str | None = None
    """Modification time as an ISO 8601 string, or ``None``.

    A string rather than a ``datetime`` so the payload is directly
    :func:`json.dumps`-able with no encoder, exactly as the reporting layer renders
    it.
    """

    content_type: str | None = None
    """Media type the artifact should be served with, when it can be served."""

    detail: str | None = None
    """Human-readable explanation of the status, when there is one to give."""


class CloneResponse(ApiResponseModel):
    """Body returned by ``POST /api/v1/clone`` -- stage ``'Clone code'``.

    Mirrors what ``app/services/clone_service.py`` returns. The service runs ``git``
    from an argument list with an explicit timeout, never through a shell, and it
    treats an existing checkout as a normal case to be fetched and reset rather than
    as an error -- which is why :attr:`action` exists.
    """

    succeeded: bool = Field(...)
    """Whether the stage completed."""

    action: CloneAction = Field(...)
    """What was actually done: a fresh clone, an update, or neither."""

    url: str = Field(...)
    """The repository that was used, REDACTED.

    ``app/services/clone_service.py`` strips any embedded credentials before
    returning, so this value is safe to log and to serve. Nothing re-derives the
    unredacted form. Which URL is the default, and why the source names two of them,
    is defect **D7** -- see :attr:`ClonePolicy.documented_url`.
    """

    branch: str | None = None
    """The branch checked out, when one was named."""

    directory: str | None = None
    """The destination directory, as a POSIX string."""

    duration_seconds: float | None = None
    """Wall-clock duration of the stage, when it was measured."""

    detail: str | None = None
    """Human-readable outcome or failure explanation, free of paths and secrets."""


class RunResponse(ApiResponseModel):
    """Body returned by ``POST /api/v1/runs`` -- stage ``'Run tests'``.

    Mirrors what ``app/services/test_runner_service.py`` returns. That service owns
    the exit-code policy; this model reports its conclusions.

    THE TWO SURPRISING SUCCESSES ARE SAID EXPLICITLY, not inferred:

    * ``succeeded=True`` with ``outcome=no_scenarios_selected`` and
      ``scenario_count=0`` is the documented default run (defect **D2**). It is
      distinguishable at a glance from ``succeeded=True`` with ``outcome=passed``
      and a positive ``scenario_count``, which is a run that actually executed
      something -- and neither is an error shape.
    * ``succeeded=True`` with ``outcome=failures_ignored`` and a positive
      ``failed_scenario_count`` is a run whose failures were tolerated (defect
      **D3**).

    Example:
        >>> zero = RunResponse(
        ...     run_id="0" * 32,
        ...     succeeded=True,
        ...     outcome=TestRunOutcome.NO_SCENARIOS_SELECTED,
        ...     exit_code=5,
        ...     tag_expression="LogOut",
        ... )
        >>> zero.succeeded, zero.scenario_count
        (True, 0)
    """

    run_id: RunId = Field(...)
    """Correlation identifier minted for this run, ``uuid.uuid4().hex``.

    A correlation value and nothing else. It never becomes a path component: the
    four artifacts live at fixed literal paths and no per-run directory is ever
    created, because relocating them would break both those literals and the
    publisher's ``fileIncludePattern`` contract ``[Jenkins:L15]``.
    """

    succeeded: bool = Field(...)
    """The stage verdict, as the runner service decided it.

    Supplied, never derived here, and deliberately independent of every count in
    this model: exit codes ``0``, ``1`` and ``5`` are all successes (defects **D2**
    and **D3**), and only ``2``, ``3`` and ``4`` are hard failures.
    """

    outcome: TestRunOutcome = Field(...)
    """Which of the six outcomes occurred, named rather than numbered."""

    exit_code: NonNegativeCount = Field(...)
    """The raw pytest exit code, reported as-is for auditability."""

    tag_expression: str = Field(...)
    """The tag expression actually applied.

    Echoed back because it is the explanation for a zero-scenario run: with the
    preserved default ``LogOut`` selector ``[README.md:L87]`` nothing matches, and a
    caller can see that from the response instead of guessing at it (defect **D2**).
    """

    workers: str | None = None
    """The worker allocation actually used, such as ``logical``."""

    scenario_count: NonNegativeCount = 0
    """Scenarios that ran. ``0`` together with ``succeeded=True`` is defect D2."""

    failed_scenario_count: NonNegativeCount = 0
    """Scenarios that failed. May be positive while ``succeeded`` is true (D3)."""

    duration_seconds: float | None = None
    """Wall-clock duration of the stage, when it was measured."""

    summary: RunSummaryModel | None = None
    """The report summary, when a report document was produced and parsed."""

    detail: str | None = None
    """Human-readable outcome explanation, free of paths and secrets."""


class RunStatusResponse(ApiResponseModel):
    """Body returned by ``GET /api/v1/runs/<run_id>`` -- run status and summary.

    Derived from the JSON report rather than from a run registry, because there is
    no registry to consult: the report artifacts live at FIXED literal paths and no
    per-run directory exists, so the status describes the most recent report and the
    identifier is carried for correlation only. Saying that plainly here is what
    stops a consumer from expecting per-run history that the preserved artifact
    layout cannot provide.

    :attr:`report_status` of ``absent`` is an ordinary answer: the artifact tree is
    wiped at the start of every run and the default invocation writes no report.
    """

    run_id: RunId = Field(...)
    """The identifier from the URL path, validated and echoed back."""

    report_status: CucumberReportStatus = Field(...)
    """Whether the report document is absent, invalid or valid."""

    summary: RunSummaryModel | None = None
    """The summary, present only when the document is valid."""

    artifacts: tuple[ReportArtifact, ...] = ()
    """The artifacts currently on disk, in the reporting layer's order."""

    detail: str | None = None
    """Human-readable explanation, free of paths and secrets."""


class ReportResponse(ApiResponseModel):
    """Body returned by ``POST /api/v1/reports`` -- stage ``'Generate report'``.

    Mirrors what ``app/services/report_service.py`` returns, and echoes the
    publisher settings actually applied so a caller can confirm the ``[Jenkins:L15]``
    parameters survived the port unchanged.

    DEFECT **D3**, PRESERVED: this stage ran unconditionally after ``'Run tests'`` in
    the source pipeline, failures and all, so the port publishes after a failed run
    too. :attr:`preceding_stage_failed` is therefore a record, never a condition,
    and :attr:`succeeded` describes the publication step alone.
    """

    succeeded: bool = Field(...)
    """Whether report generation completed."""

    run_id: str | None = None
    """The correlation identifier supplied with the request, if any.

    Typed as a plain optional string rather than as :data:`RunId` because a response
    reports what it was given; the value was already validated on the way in.
    """

    preceding_stage_failed: bool = False
    """Whether the test stage that ran before this one failed (D3).

    Recorded for the audit trail. It does not and must not affect whether the report
    is generated.
    """

    publisher: PublisherSettings = Field(default_factory=PublisherSettings)
    """The publisher parameters applied, spelled the publisher's own way."""

    artifacts: tuple[ReportArtifact, ...] = ()
    """The artifacts produced or refreshed, in the reporting layer's order."""

    summary: RunSummaryModel | None = None
    """The summary derived from the report document, when it could be derived."""

    detail: str | None = None
    """Human-readable outcome explanation, free of paths and secrets."""


# =============================================================================
# The screen-shot and error-shot index.
#
# `[README.md:L42-L43]`, verbatim: the project "generate JSON, HTML and Txt
# reporters as well. It also generate `screen shots` for your tests if you enable
# it and also generate `error shots` for your failed test cases as well."
#
# The two groups are reported SEPARATELY and are never flattened into one list,
# because their triggers differ: one is opt-in, the other fires on failure. Merging
# them would lose the distinction between a shot someone asked for and a shot that
# exists because a test broke.
#
# `app/reporting/screenshots.py` builds the index and never captures anything;
# `app/utils/paths.py` owns the two directories. The models below transport that
# index and hold no path literal of their own.
# =============================================================================


class ShotFileModel(ApiResponseModel):
    """One indexed screen shot or error shot.

    Mirrors the reporting layer's artifact view field for field, so a route can pass
    it straight through.
    """

    category: ShotGroup = Field(...)
    """Which group this shot belongs to."""

    name: str = Field(...)
    """The file name, already validated by the indexer as a safe single segment."""

    path: str = Field(...)
    """Where it lives, as a POSIX string."""

    size_bytes: NonNegativeCount = 0
    """Size in bytes."""

    modified_at: str | None = None
    """Modification time as an ISO 8601 string."""

    content_type: str | None = None
    """Media type to serve it with, inferred from the file name by the indexer."""


class ShotCollectionModel(ApiResponseModel):
    """The result of indexing one group's directory.

    An empty collection is a SUCCESSFUL result, not an error: a fresh checkout has
    no artifact tree at all, and the tree is wiped at the start of every run. The
    distinction between "no directory", "an empty directory" and "a directory that
    could not be read" is carried by :attr:`status`.

    Mirrors the reporting layer's collection view, so::

        ShotCollectionModel.model_validate(collection.as_dict())
    """

    category: ShotGroup = Field(...)
    """Which group this is, spelled as its directory name."""

    label: str = Field(...)
    """The prose wording the source uses, such as ``screen shots`` -- two words.

    Carried beside the hyphen-free or hyphenated directory name rather than
    normalised into it: the source spells the prose and the directory differently
    and both spellings are preserved.
    """

    trigger: str = Field(...)
    """The condition under which this group is produced ``[README.md:L42-L43]``."""

    directory: str = Field(...)
    """The directory that was indexed, as a POSIX string."""

    status: ShotDirectoryState = Field(...)
    """Whether the directory was absent, empty, available or unreadable."""

    exists: bool = False
    """Whether the directory exists."""

    count: NonNegativeCount = 0
    """How many shots were indexed."""

    total_size_bytes: NonNegativeCount = 0
    """Combined size of the indexed shots."""

    description: str = Field(...)
    """One-line human-readable summary produced by the indexer."""

    detail: str | None = None
    """Why the directory could not be read, when that is the status."""

    files: tuple[ShotFileModel, ...] = ()
    """The indexed shots, in the indexer's order. Empty is valid and successful."""


class ShotIndexResponse(ApiResponseModel):
    """Body returned by ``GET /api/v1/reports/<run_id>/screenshots``.

    The two groups stay under their own keys, never flattened, mirroring the
    reporting layer's index so that::

        ShotIndexResponse.model_validate(index.as_dict())

    An index reporting two absent directories and no files is a perfectly good
    ``200`` response.
    """

    screen_shots: ShotCollectionModel = Field(...)
    """The opt-in group, from the one-word directory ``[README.md:L42]``."""

    error_shots: ShotCollectionModel = Field(...)
    """The failure-driven group, from the hyphenated directory ``[README.md:L43]``."""

    total_count: NonNegativeCount = 0
    """Shots across both groups."""

    total_size_bytes: NonNegativeCount = 0
    """Combined size across both groups."""

    any_available: bool = False
    """Whether at least one shot can be served."""


# =============================================================================
# The error envelope. ONE shape, shared with the application error handlers.
#
# `app/errors.py` registers the 404, 405 and 500 handlers at APPLICATION level --
# a blueprint does not own a URL space, so a handler registered on a blueprint is
# never invoked for an unmatched URL -- and answers JSON for `/api/v1/*` with a
# machine-readable code, a human-readable message and the numeric status.
#
# The envelope below is that same shape, extended with an optional list of
# per-field particulars for the one failure mode a URL-level handler cannot
# describe: a body that did not validate. A client therefore parses ONE error
# format regardless of whether it sent a bad body, called a route that does not
# exist or triggered an internal fault. A second, competing envelope would make
# every consumer branch on which kind of failure it hit.
#
# WHAT AN ERROR MAY NOT CONTAIN, and why the constructor below is the enforcement
# point rather than a convention:
#
#   * no traceback and no exception repr -- neither is representable, because no
#     field can hold one;
#   * no filesystem path;
#   * no configuration value; and
#   * NOTHING THE CALLER SENT. pydantic's own error dictionaries carry the
#     offending `input` value and a documentation `url` by default, and `ctx` can
#     carry constraint context. All three are stripped, so a rejected body cannot
#     be reflected back to its sender and a mis-set configuration override cannot
#     be echoed into a log aggregator. Only pydantic's fixed `msg` wording, its
#     error `type` and the field location survive.
# =============================================================================


class ApiErrorDetail(ApiResponseModel):
    """One field-level particular of a rejected request body."""

    field: str | None = Field(...)
    """Dotted location of the offending field, or ``None`` for the body as a whole.

    Nested locations read ``parent.child`` and sequence positions read
    ``parent[0]``, so a caller can point at the exact element that was refused.
    """

    message: str = Field(...)
    """Pydantic's own wording for the failure, such as ``Field required``.

    Fixed wording chosen by the validation library. It never quotes the value that
    was supplied, which is what makes it safe to return and to log.
    """

    type: str = Field(...)
    """Pydantic's machine-readable error type, such as ``extra_forbidden``."""


class ApiErrorResponse(ApiResponseModel):
    """The single JSON error envelope of the ``/api/v1`` blueprint.

    Example:
        >>> from pydantic import ValidationError
        >>> try:
        ...     load_request(RunRequest, {"tags": "Login"})
        ... except ValidationError as exc:
        ...     envelope = ApiErrorResponse.from_validation_error(exc)
        >>> envelope.status, envelope.error
        (400, 'bad_request')
        >>> envelope.details[0].field, envelope.details[0].type
        ('tags', 'extra_forbidden')
    """

    error: str = Field(...)
    """Machine-readable code, the snake_case rendering of the HTTP reason phrase."""

    message: str = Field(...)
    """Human-readable summary, safe to display and free of internal detail."""

    status: int = Field(...)
    """The HTTP status accompanying this envelope."""

    details: tuple[ApiErrorDetail, ...] = ()
    """Per-field particulars, empty for errors that concern no particular field."""

    @classmethod
    def from_validation_error(
        cls,
        exception: ValidationError,
        *,
        error: str = ERROR_CODE_BAD_REQUEST,
        message: str = VALIDATION_ERROR_MESSAGE,
        status: int = HTTP_BAD_REQUEST,
    ) -> Self:
        """Build a sanitised envelope from a pydantic validation failure.

        The one place a :class:`~pydantic.ValidationError` is turned into a wire
        payload, so sanitisation is structural rather than a habit each route has to
        remember: the offending input, the documentation URL and the constraint
        context are all dropped, and only the location, the fixed wording and the
        error type survive.

        Args:
            exception: The failure raised by :func:`load_request` or by any model's
                validation.
            error: Machine-readable code. Defaults to
                :data:`ERROR_CODE_BAD_REQUEST`.
            message: Human-readable summary. Defaults to
                :data:`VALIDATION_ERROR_MESSAGE`.
            status: HTTP status. Defaults to :data:`HTTP_BAD_REQUEST`.

        Returns:
            An immutable envelope whose ``details`` preserve the order pydantic
            reported the failures in, so the first entry is the first problem found.
        """
        details = tuple(
            ApiErrorDetail(
                field=_render_error_location(entry["loc"]),
                message=entry["msg"],
                type=entry["type"],
            )
            for entry in exception.errors(
                include_url=False,
                include_input=False,
                include_context=False,
            )
        )
        return cls(error=error, message=message, status=status, details=details)


def _render_error_location(location: tuple[int | str, ...]) -> str | None:
    """Render a pydantic error location as a readable dotted path.

    Args:
        location: The ``loc`` tuple pydantic reports, whose members are field names
            and sequence indices.

    Returns:
        ``None`` when the tuple is empty, which means the error concerns the body as
        a whole rather than one of its fields -- a JSON array sent where an object
        was expected, for instance. Otherwise a path such as ``publisher.thresholds``
        or ``artifacts[0].name``.
    """
    if not location:
        return None
    rendered = ""
    for part in location:
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif rendered:
            rendered += f".{part}"
        else:
            rendered = str(part)
    return rendered
