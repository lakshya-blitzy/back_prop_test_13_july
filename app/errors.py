"""Application-level 404, 405 and 500 handlers: JSON for the API, a rendered page elsewhere.

Why this module exists at all
-----------------------------
It exists because of exactly one researched fact, recorded verbatim in Agent Action Plan
(AAP) section 0.3.2:

    "**Flask error-handler scoping.** Blueprint-level 404 and 405 handlers are **not**
    invoked for invalid URLs, because a blueprint does not own a URL space. This is why
    ``app/errors.py`` registers handlers at application level, with per-blueprint handlers
    reserved for errors raised inside blueprint views. **Had this been overlooked, unknown
    routes would have produced Flask's default HTML error page instead of the intended
    structured response.**"

A blueprint is a deferred set of registrations, not a URL namespace. When Werkzeug's map
fails to match a path there is no blueprint to attribute the failure to, so
``request.blueprint`` is ``None`` and every blueprint-scoped handler is skipped. Registering
on the *application* is therefore not a stylistic preference: it is the only registration
that fires for the case that matters most. AAP section 0.10.3 corroborates it from Flask's
own documentation - "a 404 handler registered on a blueprint does not fire for unmatched
URLs, which is why the not-found handler is registered application-wide (V11)".

Four sibling modules restate the same rule from their own side, which is how the decision
stays auditable by inspection rather than by reading this file: ``app/api/__init__.py`` ("No
error handler and no request hook are registered here"), ``app/web/routes.py`` ("All of them
live in ``app/errors.py`` at application scope"), ``app/web/__init__.py`` and
``app/extensions.py``. Nothing in this tree registers an error handler anywhere else.

ADDITIVE, with nothing to port
------------------------------
AAP section 0.4.1 classifies this file ``CREATE | ADDITIVE``: "Application-level 404/405/500
handlers; blueprint-level handlers cannot catch unmatched URLs". The pre-migration project
was a five-file Java/Maven Selenium-Cucumber scaffold - ``.gitattributes``, ``.gitignore``,
``Jenkins``, ``README.md``, ``pom.xml`` - with no HTTP server of any kind, so there is no
error-handling construct anywhere in the source to translate. The whole HTTP surface was
*derived* rather than ported, because AAP section 0.8 makes the framework mandatory: "'a
Python 3 Flask application' - Flask is mandatory, not one option among several. This is why
an HTTP surface exists at all in a system that had none (AMB-6)". Everything below is
authored against the plan; ``pom.xml`` remains a read-only parity contract (AAP AMB-4),
never compiled and never edited.

This module is the sole realization of the AAP section 0.7 enterprise baseline item B8:
"Application-level error handlers returning structured responses for the API blueprint and
rendered pages for the web blueprint". No user-specified rules were provided for this
project - the rules document is empty - so that enterprise baseline, and not an invented
rule, is what this file is held to.

Two response shapes, one set of handlers
----------------------------------------
The application serves two audiences from one URL space, so each handler branches once:

* ``/api/v1`` and everything beneath it - the eight rules of the API blueprint - answers a
  **JSON envelope**: a machine-readable ``error`` code, a human-readable ``message``, the
  numeric ``status`` and an always-present ``details`` list. That is deliberately the same
  shape ``app/api/schemas.py`` declares for ``ApiErrorResponse``, whose own comment states
  the contract this module has to keep: "A client therefore parses ONE error format
  regardless of whether it sent a bad body, called a route that does not exist or triggered
  an internal fault." The shape is reproduced here rather than imported, because importing
  ``app.api`` would invert the dependency direction and close an import cycle (see
  *Layering* below); :func:`error_code_for` derives the code from Werkzeug's own reason
  phrase, which is what makes ``bad_request``, ``not_found``, ``method_not_allowed`` and
  ``internal_server_error`` agree across the two modules without either one importing the
  other.
* Every other path - the root-mounted web blueprint's ``GET /`` and ``GET /reports`` - gets
  a **rendered page**: :data:`NOT_FOUND_TEMPLATE` or :data:`INTERNAL_SERVER_ERROR_TEMPLATE`.

Exactly three status codes, exactly two templates
-------------------------------------------------
404, 405 and 500 are the three the AAP names, and ``app/templates/errors/`` holds exactly
``404.html`` and ``500.html``. A 405 therefore has no page of its own and reuses the
not-found page. That is not a shortcut taken here; it is the contract the two templates and
the stylesheet already document from their own side - ``404.html``: "a non-API 405 is
answered with this page too", and its copy names no status code so that it stays factually
correct for either response; ``500.html``: "deliberately no third error template: a 405
reuses the 404 page"; ``main.css`` section 14: "There is deliberately no selector keyed to a
status code". Adding ``errors/405.html`` would contradict all three and break AAP section
0.8, which is binding: "No feature may be dropped, and **none may be added**." For the same
reason no handler is registered for 401, 403, 409, 422, 429 or 503.

One safety net is registered beyond those three, and it is deliberately not a taxonomy: a
handler for Werkzeug's generic :class:`~werkzeug.exceptions.HTTPException`. It **preserves
``exception.code`` exactly** - it can neither invent nor change a status - and it adds no
route, no capability and no new status code. What it does add is representational
consistency: without it, a route that calls ``abort(400)`` would answer a JSON client with
Flask's default HTML error page, which would break the one-error-format contract quoted
above. Flask's own handler lookup tries the specific code before the ``None`` bucket, so
this net can never shadow the three handlers above; and ``handle_http_exception`` returns a
:class:`~werkzeug.routing.RoutingException` before any lookup happens, so Werkzeug's
``308`` strict-slash redirects are untouched.

What never reaches the caller
-----------------------------
No traceback, no stack frame, no exception type or message, no filesystem path, no
configuration value, no environment value, no request header, cookie or session content, and
no echo of the requested address. The 500 body is a fixed generic sentence
(:data:`INTERNAL_SERVER_ERROR_MESSAGE`) that is never derived from the exception - not from
its ``description`` and never from the ``original_exception`` a Werkzeug
:class:`~werkzeug.exceptions.InternalServerError` carries. Diagnostics go to the structured
log configured by ``app/logging_config.py``, on a path the preserved ``*.log`` rule
``[.gitignore:L6]`` keeps untracked, and that log is the only place they are kept. Baseline
B5 forbids hard-coded secrets; a disclosed one would be no better, and an error response is
the classic place they escape.

What must NOT become an error
-----------------------------
Two outcomes of this system look like failures and are deliberate, preserved successes. This
module is a plausible place to get them wrong, so both are stated here:

* **Defect D2 - the zero-scenario run.** The ported default tag expression is ``LogOut``,
  from ``tags = "@LogOut"`` ``[README.md:L87]``, and it matches none of the six authored
  Gherkin tags, so pytest deselects everything and exits ``5``. AAP section 0.6: "The ported
  runner must therefore map 'everything deselected' to a **successful zero-scenario
  run**... Getting this wrong would make the ported system fail where the original
  succeeded - the most consequential single-line decision in the port." Validation criterion
  V6 grades it.
* **Defect D3 - the non-gating pipeline.** ``<testFailureIgnore>true</testFailureIgnore>``
  ``[pom.xml:L25]`` together with the six ``-1`` publisher thresholds ``[Jenkins:L15]`` mean
  test failures never fail anything, and report generation still runs after a failed test
  stage (validation criterion V12).

Both are *domain* outcomes carried in a ``200`` body, exactly as
``app/services/clone_service.py`` states for its own failures - "a ``success=False`` result
is a DOMAIN outcome, not an application error, so it is answered with ``200`` and a body --
the same rule ``app/errors.py`` states for a zero-scenario test run". Nothing in this module
inspects a run, a report or an exit code, and nothing here can turn one into a 4xx or a 5xx:
the handlers below are reached only by a real routing failure or a real unhandled exception.
A genuine server fault is still a 500 - swallowing that would be the opposite error.

Rendering an error page must never raise
----------------------------------------
An exception raised while rendering an error page is the worst failure mode available: it
turns a 404 into a 500, or sends the 500 handler round again. Two defences, one on each side
of the boundary. The templates read **no context variable whatsoever** and build no URL from
an endpoint name, so no missing name and no renamed endpoint can break them - both say so in
their own comments. And on this side, :func:`_render_error_page` catches any rendering
failure, logs it and falls back to the JSON envelope **with the same status code**. The
fallback is deliberately JSON rather than hand-built HTML: JSON needs no template engine, so
it cannot fail for the same reason the render just did, and it keeps presentation markup out
of this Python module, where AMB-9 wants none of it.

User interface scope (AMB-9)
----------------------------
AAP section 0.3.4 records that "there is no application user interface to migrate", and the
Design System Alignment Protocol is **not triggered**: no component library or design system
was named anywhere, and no attachments or design frames were provided (AAP section 0.10.1).
The rendered surface is "Plain semantic HTML with a single stylesheet. No component library,
no CSS framework, no design token system, and no client-side framework is introduced."
Accordingly this module emits no markup of its own, references no asset beyond the two
templates it renders - which inherit their entire document shell from
``app/templates/base.html`` and their presentation from ``app/static/css/main.css`` - and
adds no script, font, icon set or external request.

Layering (AAP Rule T7, baseline B4)
-----------------------------------
    "Strict one-direction internal dependencies. ``api -> services -> reporting -> utils``.
    Nothing under ``app/`` may import from ``tests/``."

This module sits outside that chain: AAP section 0.4.2's graph wires it as ``FAC --> ERR``,
reached only by the factory. It therefore imports the standard library, ``flask`` and
``werkzeug.exceptions``, and **nothing first-party at all**. It must never import
``app.api``, ``app.web``, ``app.services`` or ``app.reporting`` - the factory imports the
blueprints inside ``create_app()`` precisely to keep that cycle open - and never anything
under ``tests/`` or ``scripts/``. Nor may it import a harness-only distribution
(``pytest``, ``pytest_bdd``, ``selenium``, ``webdriver_manager``, ``faker``): those live in
``requirements-test.txt``, while the deployed container installs ``requirements.txt`` alone,
so the ``wsgi.py`` -> ``app/__init__.py`` -> this module chain has to import cleanly there.

Logging follows the same restraint. ``app/logging_config.py`` owns every handler, level and
format and states the rule for everybody else - "Anywhere else - obtain a logger, never
configure one" - so this module obtains ``logging.getLogger(__name__)`` and emits. That name
is ``app.errors``, inside the ``app`` hierarchy whose level ``configure_logging`` sets and
whose records reach the root handlers it installs, so these records land in the configured
log without this module importing it. Nothing here prints (baseline B9).

Import purity and per-application isolation
-------------------------------------------
Importing this module has no side effects: no handler is registered, no application is
built, no configuration is read, no file is opened and no directory is created. In
particular it never creates ``target/`` or any part of it - AAP section 0.6 assigns that to
exactly three owners, ``app/utils/paths.py``, the ``Makefile`` test target and
``tests/conftest.py``.

Registration happens only when the factory calls :func:`register_error_handlers`, once per
application. That is what gives every factory-built application its own handler registry,
which matters concretely: ``tests/conftest.py`` builds an isolated application per test and
each ``-n logical`` xdist worker builds its own, and AAP section 0.4.2 warns that without
the factory "fixtures would share mutable state across the parallel workers". Repeated calls
are idempotent - Flask replaces the entry for a given code rather than accumulating - so a
double call is harmless.

Usage
-----
::

    from app.errors import register_error_handlers

    def create_app(config_name: str | None = None) -> Flask:
        app = Flask(__name__)
        app.config.from_object(load_config(config_name))
        configure_logging(app)
        register_error_handlers(app)   # once, inside the factory, on the APPLICATION
        ...                            # the two blueprints are registered here, imported
        return app                     # inside the factory body to keep the cycle open

The order relative to blueprint registration does not matter: an application-scoped handler
is found by status code, not by registration sequence, so it answers a rule registered before
it and one registered after it identically.

Further reading
---------------
``docs/api.md`` is the route reference; ``docs/migration-parity.md`` is the authoritative
D1-D9 defect register and the place to read before "fixing" anything these handlers report.
"""

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from flask import (
    Flask,
    Response,
    has_request_context,
    jsonify,
    make_response,
    render_template,
    request,
)
from werkzeug.exceptions import HTTPException

# The public surface, ASCII-sorted in two groups - constants first, then callables - the same
# convention app/logging_config.py and app/config.py follow. `register_error_handlers` is the
# entry point the application factory calls; `init_app` is a documented alias of the very
# same function, mirroring the `configure_logging` / `init_logging` pairing next door, so a
# caller reaching for either name gets identical behaviour. The four handlers and the two
# helpers are exported because they are unit-testable in isolation: a test can call
# `handle_not_found` directly, or assert the discriminator with `wants_json_response`,
# without having to provoke a real routing failure first.
__all__ = [
    "API_URL_PREFIX",
    "ERROR_CODES",
    "ERROR_CODE_INTERNAL_SERVER_ERROR",
    "ERROR_CODE_METHOD_NOT_ALLOWED",
    "ERROR_CODE_NOT_FOUND",
    "HANDLED_STATUS_CODES",
    "HTTP_INTERNAL_SERVER_ERROR",
    "HTTP_METHOD_NOT_ALLOWED",
    "HTTP_NOT_FOUND",
    "INTERNAL_SERVER_ERROR_MESSAGE",
    "INTERNAL_SERVER_ERROR_TEMPLATE",
    "JSON_MIMETYPE",
    "METHOD_NOT_ALLOWED_MESSAGE",
    "NOT_FOUND_MESSAGE",
    "NOT_FOUND_TEMPLATE",
    "error_code_for",
    "handle_http_exception",
    "handle_internal_server_error",
    "handle_method_not_allowed",
    "handle_not_found",
    "init_app",
    "register_error_handlers",
    "wants_json_response",
]

# This module's own logger, obtained exactly as every other module in the tree obtains one.
# The name resolves to `app.errors`, a child of the `app` hierarchy that
# `app/logging_config.py` sets the level on, so these records reach the handlers it installs
# on the root logger - the git-ignored file plus stderr - without this module importing it.
# Nothing here prints (baseline B9).
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


# =============================================================================
# The three status codes, and nothing else.
#
# Written as named constants rather than bare literals so that the numbers in a
# registration call, in a log record and in a response body can never drift apart,
# and so the set itself is inspectable: `HANDLED_STATUS_CODES` is what a test
# asserts against, and it is the same tuple `register_error_handlers` iterates.
# =============================================================================

HTTP_NOT_FOUND: Final[int] = 404
"""No route matched the requested path - the case a blueprint-scoped handler never sees."""

HTTP_METHOD_NOT_ALLOWED: Final[int] = 405
"""A route matched the path but not the method. Has no template of its own; reuses 404's."""

HTTP_INTERNAL_SERVER_ERROR: Final[int] = 500
"""A genuine, unhandled server fault. Never a test outcome - see defects D2 and D3 above."""

HANDLED_STATUS_CODES: Final[tuple[int, ...]] = (
    HTTP_NOT_FOUND,
    HTTP_METHOD_NOT_ALLOWED,
    HTTP_INTERNAL_SERVER_ERROR,
)
"""The complete, closed set of status codes this module registers a handler for.

Exactly the three AAP sections 0.2.1 and 0.4.1 name. The generic
:class:`~werkzeug.exceptions.HTTPException` net registered alongside them is *not* a fourth
entry: it introduces no status of its own and only ever echoes ``exception.code`` back, so
the set of codes this application can produce is unchanged by its presence.
"""


# =============================================================================
# Machine-readable error codes.
#
# The snake_case rendering of Werkzeug's own reason phrase, which is exactly the
# convention `app/api/schemas.py` established with `ERROR_CODE_BAD_REQUEST =
# "bad_request"`. Deriving them from `HTTPException.name` rather than hand-listing
# them is what keeps the two modules in agreement without either importing the
# other, and it means the generic safety net produces a correct code for a status
# nobody anticipated.
# =============================================================================

ERROR_CODE_NOT_FOUND: Final[str] = "not_found"
"""Machine-readable code paired with :data:`HTTP_NOT_FOUND`."""

ERROR_CODE_METHOD_NOT_ALLOWED: Final[str] = "method_not_allowed"
"""Machine-readable code paired with :data:`HTTP_METHOD_NOT_ALLOWED`."""

ERROR_CODE_INTERNAL_SERVER_ERROR: Final[str] = "internal_server_error"
"""Machine-readable code paired with :data:`HTTP_INTERNAL_SERVER_ERROR`."""

ERROR_CODES: Final[Mapping[int, str]] = MappingProxyType(
    {
        HTTP_NOT_FOUND: ERROR_CODE_NOT_FOUND,
        HTTP_METHOD_NOT_ALLOWED: ERROR_CODE_METHOD_NOT_ALLOWED,
        HTTP_INTERNAL_SERVER_ERROR: ERROR_CODE_INTERNAL_SERVER_ERROR,
    }
)
"""Status code to machine-readable code, for the three handled statuses.

A read-only mapping, so a caller can consult it - and a test can assert it - without any
possibility of editing the vocabulary in place. :func:`error_code_for` consults it first and
falls back to deriving a code from the exception's reason phrase, which is what lets the
generic safety net answer a status that is not a key here.
"""


# =============================================================================
# Response bodies.
#
# Fixed wording, owned here. The 500 message in particular is NEVER derived from
# the exception: `app/templates/errors/500.html` states the same rule on its side,
# "the handler's exception object is never rendered - neither its description nor
# the original_exception". Fixed text is also what makes the envelope assertable
# by an integration test without pinning Werkzeug's own prose.
# =============================================================================

NOT_FOUND_MESSAGE: Final[str] = "The requested URL was not found on this server."
"""Body text for a 404. Names no path, so the requested address is never echoed back."""

METHOD_NOT_ALLOWED_MESSAGE: Final[str] = "The method is not allowed for the requested URL."
"""Body text for a 405. The permitted methods travel in the ``Allow`` header, per RFC 9110."""

INTERNAL_SERVER_ERROR_MESSAGE: Final[str] = (
    "The server encountered an internal error and could not complete the request. "
    "The detail has been recorded in the application log."
)
"""Body text for a 500. Deliberately generic and deliberately final.

It carries no traceback, no exception type or message, no filesystem path and no
configuration value, and it invents no correlation identifier to stand in for one - an
identifier that maps to nothing would be worse than none. It also says nothing about a test
run, because a 500 is a fault in this service and never a verdict on a scenario (defects D2
and D3).
"""


# =============================================================================
# Rendered pages and the API boundary.
# =============================================================================

NOT_FOUND_TEMPLATE: Final[str] = "errors/404.html"
"""The page rendered for a non-API 404 **and** for a non-API 405.

One template serves both because ``app/templates/errors/`` holds exactly two files (AAP
sections 0.2.1 and 0.3.1) and AAP section 0.8 forbids adding a third. The template is
written for that reuse: its copy names no status code, so it stays correct whichever of the
two statuses the response carries.
"""

INTERNAL_SERVER_ERROR_TEMPLATE: Final[str] = "errors/500.html"
"""The page rendered for a non-API 500."""

API_URL_PREFIX: Final[str] = "/api/v1"
"""The API blueprint's mount point, fixed by AAP section 0.3.1.

Restated here as a literal rather than imported from ``app.api``, and that duplication is
deliberate. ``app/api/__init__.py`` declares ``url_prefix`` once and states that nothing may
"reach back up into ``app``, ``app.config``, ``app.errors``..."; importing it here would
invert the dependency direction of Rule T7 and close the very import cycle the factory
avoids by importing blueprints inside ``create_app()``. The prefix is a fixed value in the
plan, not a runtime setting - it appears in no configuration surface at all - so restating
it costs nothing and keeps this module free of first-party imports.
"""

JSON_MIMETYPE: Final[str] = "application/json"
"""The content type of every structured error body, and one half of the negotiated pair."""

_HTML_MIMETYPE: Final[str] = "text/html"
"""The other half. HTML is listed first, so an ``Accept: */*`` client is offered a page."""

_NEGOTIATED_MIMETYPES: Final[tuple[str, ...]] = (_HTML_MIMETYPE, JSON_MIMETYPE)
"""Offer order for content negotiation.

Werkzeug's ``best_match`` breaks a quality tie by the order of this sequence, so a client
that expresses no preference - ``Accept: */*``, which is what curl sends - is answered with
a page, while ``Accept: application/json`` is honoured exactly.
"""

_ALLOW_HEADER: Final[str] = "Allow"
"""RFC 9110 requires a 405 to carry this header; a hand-built response would drop it."""

_EMPTY_DETAILS: Final[tuple[object, ...]] = ()
"""The always-empty ``details`` list of a URL-level error.

``app/api/schemas.py`` reserves ``details`` for per-field particulars of a rejected request
body - the one failure mode a URL-level handler cannot describe. Emitting it empty rather
than omitting it is what keeps one parse path valid for every error a client can receive.
"""


# =============================================================================
# The API-versus-web discriminator.
#
# This is the one decision every handler below delegates, so it is worth stating
# what it must NOT be built on. `request.blueprint` is precisely the value that is
# unset for an unmatched URL - a blueprint owns no URL space, which is the whole
# reason this module exists - so branching on it would send every 404 down the
# wrong arm. `request.url_rule` is unset for the same reason.
#
# What IS reliable is the request PATH, which Werkzeug has already decoded before
# routing is attempted, plus the client's own `Accept` header.
# =============================================================================


def wants_json_response() -> bool:
    """Decide whether the current request should be answered with JSON rather than a page.

    Two rules, in order:

    1. **Path prefix.** Anything at or beneath :data:`API_URL_PREFIX` is API traffic and is
       always answered with JSON, whatever the client asked for. The comparison matches the
       prefix exactly or as a complete path segment (``/api/v1`` and ``/api/v1/...``), so a
       sibling path such as ``/api/v1x/thing`` is *not* treated as API traffic.
    2. **Content negotiation.** For every other path the client chooses. HTML is offered
       first, so a browser (``Accept: text/html,...``) and an unopinionated client
       (``Accept: */*``, or no ``Accept`` header at all) both receive the rendered page,
       while a client that explicitly prefers ``application/json`` receives the envelope.

    Returns:
        ``True`` to answer with the JSON envelope, ``False`` to render a page.

    Note:
        Outside a request context the answer is ``True``. Flask invokes error handlers only
        from inside one, so this branch is unreachable in normal operation; it exists because
        the alternative to answering it is an :class:`AttributeError` from ``request``, and a
        crash inside an error handler is the failure mode this module works hardest to avoid.
        JSON is the correct fallback because rendering needs an application context that may
        equally be absent, and because a caller with no request cannot be a browser.
    """
    if not has_request_context():
        return True
    path = request.path
    if path == API_URL_PREFIX or path.startswith(f"{API_URL_PREFIX}/"):
        return True
    accepted = request.accept_mimetypes
    if not accepted:
        # No `Accept` header at all. Werkzeug models that as an empty accept list, for which
        # `best_match` can only return its default, so the decision is made here instead:
        # an unstated preference is treated as "no preference", which is HTML.
        return False
    return accepted.best_match(_NEGOTIATED_MIMETYPES, default=_HTML_MIMETYPE) == JSON_MIMETYPE


def error_code_for(exception: HTTPException | None = None, status: int | None = None) -> str:
    """Return the machine-readable error code for a status or an HTTP exception.

    The code is the snake_case rendering of the HTTP reason phrase - ``not_found``,
    ``method_not_allowed``, ``internal_server_error``, and for the generic safety net
    whatever Werkzeug calls the status it was handed (``bad_request``, ``forbidden``, ...).
    That is the same convention ``app/api/schemas.py`` uses for ``ERROR_CODE_BAD_REQUEST``,
    which is how the two modules agree on one vocabulary without importing each other.

    Args:
        exception: The exception being handled, if there is one. Its ``code`` is preferred
            over *status* when both are given, and its ``name`` is what a status outside
            :data:`ERROR_CODES` is derived from.
        status: An explicit status code, used when no exception is available or when the
            exception carries no code of its own.

    Returns:
        A non-empty snake_case code. For a status this module knows, the corresponding
        :data:`ERROR_CODES` entry; for any other status, the derived reason phrase; and
        :data:`ERROR_CODE_INTERNAL_SERVER_ERROR` when neither argument identifies a status
        at all, because a request that cannot be classified has already gone wrong on this
        side of the wire.

    Example:
        >>> error_code_for(status=404)
        'not_found'
        >>> from werkzeug.exceptions import Forbidden
        >>> error_code_for(Forbidden())
        'forbidden'
    """
    resolved = status
    if exception is not None and exception.code is not None:
        resolved = exception.code
    if resolved is None:
        return ERROR_CODE_INTERNAL_SERVER_ERROR
    known = ERROR_CODES.get(resolved)
    if known is not None:
        return known
    derived = _snake_case(exception.name if exception is not None else "")
    return derived or ERROR_CODE_INTERNAL_SERVER_ERROR


def _snake_case(phrase: str) -> str:
    """Render an HTTP reason phrase as a snake_case identifier.

    ``"Not Found"`` becomes ``not_found`` and ``"Bad Request"`` becomes ``bad_request``,
    matching ``app/api/schemas.py``'s ``ERROR_CODE_BAD_REQUEST`` exactly. Punctuation is
    dropped rather than transliterated and whitespace becomes a single underscore, so
    ``"I'm a Teapot"`` yields ``im_a_teapot`` - a machine-readable field never carries an
    apostrophe or a hyphen into a client's parser.

    Args:
        phrase: The reason phrase, which may be empty.

    Returns:
        The snake_case rendering, or the empty string when *phrase* holds nothing usable.
        Callers treat the empty string as "no code could be derived" rather than as a code.
    """
    words = (
        "".join(character for character in word if character.isalnum()) for word in phrase.split()
    )
    return "_".join(word for word in words if word).lower()


# =============================================================================
# Response construction.
#
# Two builders, one per audience, and a single branch point in `_error_response`
# so that no handler can accidentally answer the API with a page or a browser with
# a payload. Every builder returns a fully formed `Response` with its status
# already set, rather than a `(body, status)` tuple: the status then travels with
# the object through the `Allow`-header step below and cannot be lost on the way.
# =============================================================================


def _json_error_response(status: int, error: str, message: str) -> Response:
    """Build the structured error body for the ``/api/v1`` blueprint.

    The envelope is ``{"error", "message", "status", "details"}`` - deliberately the shape
    ``app/api/schemas.py`` declares for ``ApiErrorResponse``, so that a client parses one
    error format whether it sent a bad body, called a route that does not exist or triggered
    an internal fault. ``details`` is always present and, for a URL-level error, always
    empty: it is reserved for the per-field particulars of a rejected request body, which is
    the one failure mode this module cannot describe.

    Args:
        status: The HTTP status to set on the response.
        error: The machine-readable code, from :func:`error_code_for`.
        message: The human-readable summary. Always module-owned text for a 500; never a
            traceback, a path or a configuration value for any status.

    Returns:
        A JSON response carrying :data:`JSON_MIMETYPE`, with *status* applied.
    """
    response = jsonify(
        error=error,
        message=message,
        status=status,
        details=_EMPTY_DETAILS,
    )
    response.status_code = status
    return response


def _render_error_page(template: str, status: int, error: str, message: str) -> Response:
    """Render an error page, falling back to the JSON envelope if rendering fails.

    Both templates are written to be unbreakable - each reads no context variable at all and
    builds no URL from an endpoint name - so this is defence in depth rather than a suspicion
    about them. What it defends against is the class of failure that would otherwise be
    catastrophic: a missing template file, a syntax error introduced in the shared layout, or
    an absent ``static`` endpoint would each raise *inside* the error handler, turning a 404
    into a 500 or sending the 500 handler round for a second time.

    The fallback is JSON rather than hand-written HTML for two reasons. It needs no template
    engine, so it cannot fail for the same reason the render just did; and it keeps
    presentation markup out of this module, which AMB-9 requires - the rendered surface is
    the two templates plus ``app/static/css/main.css`` and nothing else.

    Args:
        template: :data:`NOT_FOUND_TEMPLATE` or :data:`INTERNAL_SERVER_ERROR_TEMPLATE`.
        status: The HTTP status to set on the response. Preserved across the fallback, so a
            rendering failure can never change the status a client sees.
        error: The machine-readable code, used only by the fallback body.
        message: The human-readable summary, used only by the fallback body.

    Returns:
        An HTML response with *status* applied, or the JSON envelope for the same status when
        rendering raised.
    """
    try:
        # No context is passed, deliberately. Both templates document that they read no
        # variable whatsoever, precisely so that nothing this module forgets to supply can
        # break them, and so that no request detail can be reflected into the page.
        return make_response(render_template(template), status)
    # Deliberately broad. An error page must degrade, never propagate: any exception at all
    # from the template engine has to become a response, because there is no outer handler
    # left to recover from one raised in here.
    except Exception:
        _LOGGER.exception(
            "Failed to render error page %r for status %d; answering with the JSON envelope "
            "instead. The status code is unchanged.",
            template,
            status,
        )
        return _json_error_response(status, error, message)


def _error_response(status: int, error: str, message: str, template: str) -> Response:
    """Answer the current request with either the JSON envelope or a rendered page.

    The single branch point of this module. Every handler funnels through it, which is what
    guarantees that the API and the web surface can never be answered in each other's
    representation, and that the branch is decided in exactly one documented place.

    Args:
        status: The HTTP status to set on the response.
        error: The machine-readable code, from :func:`error_code_for`.
        message: The human-readable summary.
        template: The page to render when the request is not API traffic.

    Returns:
        A fully formed response with *status* applied.
    """
    if wants_json_response():
        return _json_error_response(status, error, message)
    return _render_error_page(template, status, error, message)


def _allowed_methods(exception: HTTPException) -> str | None:
    """Render the ``Allow`` header value a 405 must carry, if the exception supplies one.

    RFC 9110 requires a ``405`` response to advertise the methods the target resource does
    support, and Werkzeug's :class:`~werkzeug.exceptions.MethodNotAllowed` carries them in
    ``valid_methods``. That header is added by the exception's *own* response object, so a
    handler that builds a response instead - which every handler here does - silently drops
    it; restoring it is not optional politeness but part of the status's contract.

    Args:
        exception: The exception being handled. Only ``MethodNotAllowed`` defines
            ``valid_methods``; :class:`~werkzeug.exceptions.NotFound` and the rest do not,
            hence the tolerant lookup rather than an attribute access.

    Returns:
        A comma-separated list such as ``"GET, HEAD, OPTIONS"``, or ``None`` when the
        exception names no methods - in which case no header is set, which is preferable to
        advertising an empty set.
    """
    methods = getattr(exception, "valid_methods", None)
    if not methods:
        return None
    # Sorted, deliberately. Werkzeug derives `valid_methods` from a set, so the order it
    # produces varies between processes with the hash seed - which would make an assertion on
    # this header intermittently fail and a captured response diff churn for no reason. RFC
    # 9110 assigns no meaning to the order, only to the membership, so sorting changes nothing
    # a client may rely on and makes the header reproducible.
    return ", ".join(sorted(str(method) for method in methods))


def _describe_request() -> str:
    """Render the current request as one short, log-safe string.

    Args:
        None.

    Returns:
        ``"<METHOD> <path>"`` with the path passed through :func:`repr`, or a fixed
        placeholder outside a request context. The ``repr`` is the point: a percent-encoded
        newline in a URL decodes into ``request.path``, and interpolating that raw into a log
        line would let a caller forge a second record. ``repr`` escapes it. Only the method
        and the path are included - never the query string, a header, a cookie or a body -
        because a log record is not the place to accumulate whatever a client chose to send.
    """
    if not has_request_context():
        return "<no request context>"
    return f"{request.method} {request.path!r}"


# =============================================================================
# The handlers.
#
# Each is an ordinary module-level function, annotated to accept `Exception`
# because that is the widest thing Flask can hand one: for an unmatched URL it
# passes Werkzeug's `NotFound`, and for an unhandled view exception it passes an
# `InternalServerError` wrapping the original. None of them constructs an
# application, registers anything, or reads configuration - registration is
# `register_error_handlers`'s job and happens once, from the factory.
#
# Being module-level rather than closures defined inside the registration function
# is what makes them directly unit-testable: a test can call one inside a
# `test_request_context` and assert the response without provoking real routing.
# =============================================================================


def handle_not_found(exception: Exception) -> Response:
    """Answer a request whose path matched no route: JSON under ``/api/v1``, else a page.

    This is the handler validation criterion V11 grades, and the reason this module exists.
    It only ever fires because it is registered on the *application*: an unmatched URL
    belongs to no blueprint, so a blueprint-scoped 404 handler would be skipped and Flask
    would fall back to its own default HTML error page - for a JSON client as readily as for
    a browser.

    Args:
        exception: The :class:`~werkzeug.exceptions.NotFound` Werkzeug raised, or any
            exception a caller routes here. Its ``description`` is used when it carries one,
            so a deliberate ``abort(404, description=...)`` from a route survives to the
            client; Werkzeug's own default description names no path, so nothing about the
            requested address is echoed back either way.

    Returns:
        A 404 response - the JSON envelope for API traffic, the rendered
        :data:`NOT_FOUND_TEMPLATE` otherwise.
    """
    # INFO, not WARNING. A 404 is routine and client-caused: a browser requests
    # /favicon.ico unprompted on every page view, and any exposed service is probed
    # continuously. Recording that at WARNING would train readers to ignore warnings, which
    # is exactly what must not happen to the 405 and 500 records below.
    _LOGGER.info(
        "%s -> %d %s (no route matched)",
        _describe_request(),
        HTTP_NOT_FOUND,
        ERROR_CODE_NOT_FOUND,
    )
    return _error_response(
        HTTP_NOT_FOUND,
        ERROR_CODE_NOT_FOUND,
        _description_or(exception, NOT_FOUND_MESSAGE),
        NOT_FOUND_TEMPLATE,
    )


def handle_method_not_allowed(exception: Exception) -> Response:
    """Answer a request whose path matched a route but whose method did not.

    The ``Allow`` header RFC 9110 requires is restored from the exception's ``valid_methods``,
    because building a response by hand drops the one the exception would have set itself.

    A non-API 405 renders :data:`NOT_FOUND_TEMPLATE`. That is the documented contract of the
    template itself - "a non-API 405 is answered with this page too" - and of
    ``app/static/css/main.css`` section 14; ``app/templates/errors/`` holds exactly two files
    and AAP section 0.8 forbids adding a third, so the page's copy names no status code and
    stays correct for either response.

    Args:
        exception: The :class:`~werkzeug.exceptions.MethodNotAllowed` Werkzeug raised. Its
            ``valid_methods`` supplies the ``Allow`` header when present.

    Returns:
        A 405 response, carrying ``Allow`` whenever the exception named any methods.
    """
    # WARNING, unlike the 404 above. A wrong method on a route that exists has no benign
    # automatic source - no browser or crawler produces one - so it means a real client bug
    # and is worth surfacing.
    _LOGGER.warning(
        "%s -> %d %s (route exists, method rejected; allow=%s)",
        _describe_request(),
        HTTP_METHOD_NOT_ALLOWED,
        ERROR_CODE_METHOD_NOT_ALLOWED,
        _allowed_methods(exception) if isinstance(exception, HTTPException) else None,
    )
    response = _error_response(
        HTTP_METHOD_NOT_ALLOWED,
        ERROR_CODE_METHOD_NOT_ALLOWED,
        _description_or(exception, METHOD_NOT_ALLOWED_MESSAGE),
        NOT_FOUND_TEMPLATE,
    )
    if isinstance(exception, HTTPException):
        allowed = _allowed_methods(exception)
        if allowed is not None:
            response.headers[_ALLOW_HEADER] = allowed
    return response


def handle_internal_server_error(exception: Exception) -> Response:
    """Answer a genuine server fault with a generic 500 and put the detail in the log.

    The body is :data:`INTERNAL_SERVER_ERROR_MESSAGE`, always, and is never derived from the
    exception - not from its ``description`` and never from the ``original_exception`` a
    Werkzeug :class:`~werkzeug.exceptions.InternalServerError` carries. That mirrors what
    ``app/templates/errors/500.html`` states on its own side, and it is what stops a
    traceback, a filesystem path or a configuration value from escaping in a response body.

    The full diagnostic goes to the structured log instead, with the traceback attached
    whenever an original exception is available. Flask's own ``log_exception`` may already
    have written a companion record naming the *request*; the duplication is accepted
    deliberately, because that record is the framework's to emit or withhold - it is skipped
    entirely for an explicitly raised ``InternalServerError`` - whereas the guarantee that a
    500 is never silent belongs to this module.

    A 500 here is a fault in this service and never a verdict on a test run. A run that
    selected no scenario (defect D2) and a run whose scenarios failed (defect D3) are both
    successes carried in a ``200`` body, and nothing in this module can convert either into
    an error.

    Args:
        exception: The :class:`~werkzeug.exceptions.InternalServerError` Flask constructed,
            whose ``original_exception`` is the unhandled exception when there was one, or a
            bare exception a caller routes here.

    Returns:
        A 500 response - the JSON envelope for API traffic, the rendered
        :data:`INTERNAL_SERVER_ERROR_TEMPLATE` otherwise. Neither body carries any detail.
    """
    original = getattr(exception, "original_exception", None)
    cause = original if isinstance(original, BaseException) else None
    # ERROR, and the only place in this module that attaches a traceback. `exc_info` takes the
    # original exception rather than the InternalServerError wrapper, so the recorded frames
    # are the ones that actually failed; when there is no original - `abort(500)` - there is
    # no traceback to attach and the record stands on its own.
    _LOGGER.error(
        "%s -> %d %s (%s)",
        _describe_request(),
        HTTP_INTERNAL_SERVER_ERROR,
        ERROR_CODE_INTERNAL_SERVER_ERROR,
        type(cause).__name__ if cause is not None else type(exception).__name__,
        exc_info=cause,
    )
    return _error_response(
        HTTP_INTERNAL_SERVER_ERROR,
        ERROR_CODE_INTERNAL_SERVER_ERROR,
        INTERNAL_SERVER_ERROR_MESSAGE,
        INTERNAL_SERVER_ERROR_TEMPLATE,
    )


def handle_http_exception(exception: HTTPException) -> Response:
    """Answer any other HTTP error in this application's own representation.

    The documented safety net, and deliberately not the start of an error taxonomy. It
    registers no status of its own: it echoes ``exception.code`` back unchanged, so the set of
    statuses this application can produce is exactly what its routes and Werkzeug's routing
    already produce, and :data:`HANDLED_STATUS_CODES` remains the closed set of codes this
    module has an opinion about. No route, no capability and no user-visible feature is added
    - AAP section 0.8 forbids that - only the *representation* is normalised.

    Normalising it matters because of the contract ``app/api/schemas.py`` records: "A client
    therefore parses ONE error format regardless of whether it sent a bad body, called a
    route that does not exist or triggered an internal fault." Without this net, a route that
    calls ``abort(400)`` would answer a JSON client with Flask's default HTML error page and
    that contract would hold only for the three statuses above.

    It cannot shadow those three. Flask's handler lookup consults the specific status code
    before the generic bucket, so ``NotFound``, ``MethodNotAllowed`` and ``InternalServerError``
    continue to reach their own handlers. Werkzeug's routing redirects are unaffected for a
    different reason: :class:`~werkzeug.routing.RoutingException` is returned by Flask before
    any handler lookup happens, so a ``308`` strict-slash redirect is never seen here.

    Args:
        exception: The :class:`~werkzeug.exceptions.HTTPException` being handled. A subclass
            with no numeric ``code`` - Werkzeug allows one - is treated as a 500, because a
            response has to carry some status and an unclassifiable failure is this server's.

    Returns:
        A response whose status is ``exception.code``, with a 5xx rendering
        :data:`INTERNAL_SERVER_ERROR_TEMPLATE` and everything else rendering
        :data:`NOT_FOUND_TEMPLATE` - the only two pages that exist.
    """
    status = exception.code if exception.code is not None else HTTP_INTERNAL_SERVER_ERROR
    if status == HTTP_INTERNAL_SERVER_ERROR:
        # Reached only if a bare `InternalServerError` subclass without its own code lands
        # here. Delegating keeps the "500 bodies are generic and 500s are logged with a
        # traceback" rules in exactly one place.
        return handle_internal_server_error(exception)
    error = error_code_for(exception)
    template = (
        INTERNAL_SERVER_ERROR_TEMPLATE
        if status >= HTTP_INTERNAL_SERVER_ERROR
        else NOT_FOUND_TEMPLATE
    )
    # Server faults are logged at ERROR; a client error is the caller's to fix, so WARNING.
    # No traceback is attached: an HTTPException raised on purpose is a control-flow decision,
    # not a crash, and there is no original exception behind it.
    _LOGGER.log(
        logging.ERROR if status >= HTTP_INTERNAL_SERVER_ERROR else logging.WARNING,
        "%s -> %d %s (handled by the generic HTTP safety net)",
        _describe_request(),
        status,
        error,
    )
    if status >= HTTP_INTERNAL_SERVER_ERROR:
        # Same disclosure rule as the dedicated 500 handler: a server fault's description may
        # have been built from internal state, so it never reaches the caller.
        message = INTERNAL_SERVER_ERROR_MESSAGE
    else:
        # `include_default=True`: this module owns no wording for a status it was never asked
        # to handle, so Werkzeug's own description is the best text available, with the reason
        # phrase as a last resort. Contrast the three handled statuses above, whose bodies are
        # module-owned constants precisely so that they stay deterministic.
        message = _description_or(exception, exception.name, include_default=True)
    response = _error_response(status, error, message, template)
    if status == HTTP_METHOD_NOT_ALLOWED:
        allowed = _allowed_methods(exception)
        if allowed is not None:
            response.headers[_ALLOW_HEADER] = allowed
    return response


def _description_or(exception: Exception, fallback: str, *, include_default: bool = False) -> str:
    """Return an HTTP exception's description, or *fallback* when it has nothing usable.

    Werkzeug's ``description`` is the documented way for a route to say *why* it aborted -
    ``abort(404, description="no report for that run")`` - and discarding it outright would
    make that mechanism useless across the whole API. But an exception Werkzeug raised for a
    routing failure also carries a description: its own class default, a two-sentence stock
    paragraph. Letting that through would mean the body of the commonest 404 in the system was
    third-party prose rather than :data:`NOT_FOUND_MESSAGE`, so a test asserting the envelope
    would be pinning Werkzeug's wording and would break on a Werkzeug upgrade.

    Hence the distinction *include_default* draws. By default only a description a caller set
    **explicitly** is honoured, detected by comparing against the class attribute the
    exception type declares; a stock description falls through to the module-owned *fallback*,
    which keeps the three handled statuses deterministic and assertable. The generic safety
    net passes ``include_default=True`` instead, because this module owns no wording for a
    status it was never asked to handle, so Werkzeug's own description is the best available
    text there.

    Neither mode is ever used for a 500. :func:`handle_internal_server_error` and the 5xx
    branch of :func:`handle_http_exception` both pass a module-owned message and ignore
    whatever the exception carries, because a server fault's description may have been
    assembled from internal state.

    Args:
        exception: The exception being handled. Anything that is not an
            :class:`~werkzeug.exceptions.HTTPException`, or one whose description is empty,
            not a string, or (unless *include_default*) merely the type's default, yields
            *fallback*.
        fallback: The module-owned message to use instead.
        include_default: Whether the exception type's stock description counts as usable.

    Returns:
        A non-empty message, stripped of surrounding whitespace.
    """
    if isinstance(exception, HTTPException):
        description = exception.description
        if isinstance(description, str) and description.strip():
            if include_default or description != getattr(type(exception), "description", None):
                return description.strip()
    return fallback


# =============================================================================
# Registration - the single entry point, and the load-bearing line of this module.
#
# Every handler above is attached to the APPLICATION object and to nothing else.
# `Blueprint.errorhandler` and `Blueprint.app_errorhandler` are deliberately never
# used here, and no blueprint in this tree registers a handler of its own: a
# blueprint owns no URL space, so a blueprint-scoped 404 or 405 is simply skipped
# when routing fails and Flask's own default error page is returned instead.
#
# Nothing is registered at import time. The factory calls in once per application,
# which is what gives each factory-built application its own registry - required by
# `tests/conftest.py`, which builds an isolated application per test, and by every
# `-n logical` xdist worker, which builds its own.
# =============================================================================


def register_error_handlers(app: Flask) -> None:
    """Register this module's 404, 405 and 500 handlers on *app* itself.

    Called once from ``create_app()``, with the application object - never with a blueprint.
    Flask stores an application-scoped handler under the ``None`` key of
    ``app.error_handler_spec``, so that registry is where a test confirms these handlers
    landed at application scope and that nothing was registered under a blueprint key.

    Idempotent: Flask replaces the entry for a given status rather than accumulating, so
    calling this twice on one application leaves exactly one handler per status. Like every
    Flask setup call it must run before the application handles its first request, which the
    factory guarantees by calling it during construction.

    Args:
        app: The application to register on. Any Flask instance is accepted, so a test can
            register on a bare application without going through the factory.

    Returns:
        ``None``. The application is mutated in place, matching ``configure_logging(app)``
        next door rather than returning something to chain.
    """
    app.register_error_handler(HTTP_NOT_FOUND, handle_not_found)
    app.register_error_handler(HTTP_METHOD_NOT_ALLOWED, handle_method_not_allowed)
    app.register_error_handler(HTTP_INTERNAL_SERVER_ERROR, handle_internal_server_error)
    # The safety net, registered last and by exception class rather than by status, which is
    # what puts it in Flask's `None` bucket. Flask consults the specific-status bucket first,
    # so the three registrations above always win for their own statuses and this one only
    # ever answers a status none of them claims - echoing that status back unchanged.
    app.register_error_handler(HTTPException, handle_http_exception)
    _LOGGER.debug(
        "Registered application-scoped error handlers for %s plus the generic HTTPException "
        "safety net on %r; JSON is served for %s and a rendered page elsewhere.",
        ", ".join(str(status) for status in HANDLED_STATUS_CODES),
        app.name,
        API_URL_PREFIX,
    )


# Documented alias of `register_error_handlers`, mirroring the `configure_logging` /
# `init_logging` pairing in app/logging_config.py and Flask's own `init_app(app)` extension
# convention. It is the same function object, not a wrapper, so the two names cannot drift
# apart and either spelling behaves identically for the factory.
init_app = register_error_handlers
