"""Application-level HTTP error handlers for the read-only artifact viewer.

Two handlers and one entry point: 404 for a request the viewer cannot serve,
500 for a request that broke while being served, and nothing else.
:func:`register_error_handlers` is the whole public surface, called from
``create_app()`` in ``app/__init__.py``, which AAP 0.4.2 makes the sole place
the viewer is wired together.  The dependency runs one way: this module
defines no route, imports no blueprint and is imported by no view.  There is
no wider taxonomy - no 400, 403, 405 or 503 handler, no problem-details
envelope, no error identifier, no monitoring hook - because the HTTP surface
is held to the read-only minimum (AAP deviation 12, under its Conflict 3).

One response for every cause
----------------------------
Most 404s here are not a mistyped URL.  AAP 0.3.1 gives all four report
routes one data-availability rule - an absent, unreadable or unparseable
results artifact answers 404, *"the same response for all three causes"* - to
which the index routes add an out-of-range feature or scenario index, and the
artifact route adds a name off the allowlist, an allowlisted name with nothing
behind it, a directory, a path resolving outside the artifact root, and any
worker-intermediates path.  Ten causes, one response: no cause, code or
diagnostic detail reaches the body, a rejected traversal is answered 404
rather than 403, and neither the rejected name nor any filesystem path is
echoed, so a probe cannot be told from an honest mistake.  Nothing here
consults the request in order to be helpful; diagnosis lives in the log, where
the rejecting route records what it detected.

Content negotiation
-------------------
Five of the six routes answer with HTML and one - ``web.reports_summary`` -
answers with JSON, and that JSON route is governed by the data-availability
rule too, so it can 404.  An HTML error page is the wrong body for a client
that asked for JSON, so the negotiation is minimal and has exactly two inputs:

1. The failing endpoint.  A request matched to an endpoint listed in
   :data:`JSON_ENDPOINTS` is answered with JSON whatever it sent, so the error
   body of a JSON route has the media type its success body has.  The endpoint
   is compared as a string; this module imports nothing from ``app.web``, and
   the endpoint names are a fixed contract that package's own documentation
   calls a contract rather than a preference.
2. The ``Accept`` header, when the endpoint decides nothing - which includes
   every unmatched URL, where there is no endpoint at all.  JSON is chosen
   only when the header prefers it *strictly* over HTML.  The strictness
   matters: ``*/*`` and a missing header both weigh the two media types
   equally, and both must fall to HTML, which is what a browser and the test
   client respectively send.

The JSON body is a small object carrying the same cause-neutral message and
the status code.  It is not a problem-details document; RFC 7807 is beyond the
minimum Conflict 3 compels.

One cache policy, on every body this module renders
---------------------------------------------------
An error body is not neutral in what it discloses to a cache.  A 404 rendered
here is most often the data-availability answer for a run whose artifacts have
just been removed, and the 500 page is served for a request that broke while
rendering run evidence, so neither may be kept by a browser or a private
intermediary after the run it describes is gone (CWE-525).  Every response
built below therefore carries ``Cache-Control: no-store, private, max-age=0``
and the HTTP/1.0 ``Pragma: no-cache``, on the JSON body, the HTML page and the
plain-text fallback alike.

The policy is declared here as a local constant rather than imported from
``app/web/routes.py``, which states the same one for its own responses, and for
exactly the reason :data:`JSON_ENDPOINTS` is a literal: this module must not
import the blueprint or its view module.  The route module's hook cannot cover
these bodies anyway - an unmatched URL matches no blueprint, so no
blueprint-level hook ever runs for it - which is why the statement exists twice
and must stay identical in both places.

The internal-error handler
--------------------------
The response says that the request could not be completed and that the problem
was recorded, and says nothing else: no exception type, no exception message,
no traceback, no frame, no request detail, no configuration value, no path, no
version and no host.  The diagnostic exists, in the log, where an operator can
read it and a caller cannot.

Logging is acquired from the standard library - ``logging.getLogger(__name__)``
- and ``app/logging_config.py`` is deliberately not imported: that module
installs handlers, is called once per process by that process's own entry
point - for this module's purposes, ``create_app()`` - and every other module
in the port acquires its logger directly.  Its handler split sends WARNING and
above to the error stream, so the traceback this module records lands there
without this module knowing anything about streams, and the sanitizing
formatter on that handler bounds and renders the traceback safely without this
module preparing it.

What either handler's record says about the request is bounded too: the
method, the path with its query string removed, and the matched endpoint - and
nothing else.  A query string is caller-supplied text that may carry a token, a
credential or personal data, and a log is persisted and shipped, so the raw
query is never written to one; no route in the viewer reads a query parameter,
so nothing diagnostic is lost by leaving it out.

The handler does not re-raise, does not retry, and attempts no recovery: every
route is synchronous and read-only, so a 500 here is a defect in a reader or a
template rather than a condition a caller can fix by trying again.  There is no
debug branch either.  Flask's own debug behaviour is Flask's business; these
handlers never disclose internals, whatever the configuration says.

A handler that cannot itself fail
---------------------------------
Both pages render from the shared shell, and the shell builds every URL with
``url_for``, so rendering has a failure mode that has nothing to do with the
original error: an application whose ``web`` blueprint is not registered raises
while building those URLs.  If that were left unguarded, a 404 would become a
500, and a 500 would become a bare framework traceback - exactly the outcome
the internal-error page exists to prevent.  Every render is therefore guarded,
and a failure to render is logged and answered with a plain-text body carrying
the same cause-neutral message at the same status.  The fallback discloses
nothing, changes no status code, and is invisible to a correctly wired
application.

What this module must not do
----------------------------
No route, no blueprint and no route decorator.  No filesystem access, no
import of ``app/utils/paths.py`` and no path literal - AAP 0.4.2 makes that
module the sole owner of artifact paths and this file has no reason to name
one.  No import of ``app.web``, ``app.services``, ``app.reporting``,
``app.pages``, ``app.automation`` or ``app.logging_config``.  No ``selenium``
and no ``behave``.  No module-level mutable state, so registering handlers on
one application cannot affect another.

Acceptance criteria
-------------------
Stated here so that ``tests/test_web_routes.py``, which owns the assertions,
implements them faithfully:

1. **404 on every cause, indistinguishable.**  With no results artifact, then
   with an unreadable one, then with a malformed one, all four report routes
   answer 404 and the three bodies are byte-identical for a given route.
2. **404 on out-of-range indices.**  A feature index past the end of the list,
   and a valid feature index with an out-of-range scenario index.
3. **Artifact allowlist.**  404 for a name off the allowlist, for the report
   tree's siblings, for an absent file, for a traversal that resolves outside
   the artifact root, and for any worker-intermediates path - with no absolute
   filesystem path in the body and no echo of the rejected name.
4. **Negotiation.**  A 404-producing report route answers JSON under
   ``Accept: application/json`` and the rendered template under
   ``Accept: text/html``; the summary route answers JSON whatever it sent.
5. **500 handler.**  Status 500, the rendered page, no traceback text in the
   body, and the traceback present in the log.
6. **Registration.**  After ``create_app()`` the handler map carries 404 and
   500, and building a second application registers handlers on it without
   either application affecting the other.
7. **The index still answers 200** with nothing generated - the one route the
   data-availability rule does not govern, and proof that these handlers do
   not intercept it.
8. **Import boundary.**  No ``app.web``, ``app.services``, ``app.reporting``,
   ``app.pages``, ``app.automation`` or ``app.logging_config`` import, no
   ``selenium``, no ``behave``, and no path literal naming the output
   directory.
9. **Cache policy.**  Every 404 and 500 answered here - JSON, HTML and the
   plain-text fallback - carries ``Cache-Control: no-store, private,
   max-age=0`` and ``Pragma: no-cache``, including for an unmatched URL, which
   no blueprint hook can reach.
10. **Coverage.**  This module sits outside the four gated packages, so no
    numeric threshold applies to it; both handlers are still covered on both
    negotiation branches.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Final

from flask import Flask, Response, jsonify, render_template, request

__all__ = ["register_error_handlers"]

logger = logging.getLogger(__name__)

NOT_FOUND_TEMPLATE: Final[str] = "errors/404.html"

INTERNAL_ERROR_TEMPLATE: Final[str] = "errors/500.html"

#: Endpoints whose successful response is JSON, and whose errors therefore are
#: too.  AAP 0.3.1's route table carries exactly one: ``GET /reports/summary``.
#: Held as an endpoint-name string rather than reached through an import,
#: because this module must not import the blueprint; ``app/web/__init__.py``
#: fixes the same names and calls them a contract rather than a preference.
JSON_ENDPOINTS: Final[frozenset[str]] = frozenset({"web.reports_summary"})

NOT_FOUND_MESSAGE: Final[str] = (
    "Not found. This viewer renders only the report artifacts a completed "
    "test run has already written, and it has nothing to show for this "
    "request. Runs are started from the command line with run-tests."
)

INTERNAL_ERROR_MESSAGE: Final[str] = (
    "Internal server error. The problem has been recorded in the server log, "
    "and no details of it are reported here."
)

_JSON_MIMETYPE: Final[str] = "application/json"
_HTML_MIMETYPE: Final[str] = "text/html"
_TEXT_MIMETYPE: Final[str] = "text/plain"

#: What every body this module renders tells a cache.  ``no-store`` rather
#: than ``no-cache``: RFC 9111 makes ``no-cache`` a revalidation requirement,
#: so the representation is still written to disk and merely checked before
#: reuse, while ``no-store`` forbids keeping it at all.  ``private`` bars a
#: shared cache from holding it even where a proxy ignores the first
#: directive, and ``max-age=0`` is the same statement for a cache that
#: predates them both.  ``app/web/routes.py`` declares this policy separately
#: for its own responses, for the reason :data:`JSON_ENDPOINTS` is a literal:
#: this module imports nothing from ``app.web``.
_CACHE_CONTROL_POLICY: Final[str] = "no-store, private, max-age=0"

#: The HTTP/1.0 spelling, for an intermediary that understands nothing newer.
_PRAGMA_POLICY: Final[str] = "no-cache"

#: The two header names, written once so the three body builders below cannot
#: spell either of them differently.
_CACHE_CONTROL_HEADER: Final[str] = "Cache-Control"
_PRAGMA_HEADER: Final[str] = "Pragma"

#: Log line for a routine not-found.  DEBUG rather than WARNING on purpose: a
#: 404 is the viewer's ordinary answer before a run has produced results, and
#: at WARNING every such request would be an entry on the error stream.  The
#: security-relevant detail of a rejection - which allowlist rule refused a
#: name, which path escaped the artifact root - is logged by the route that
#: detected it, since that is the only code that knows.
#:
#: Three values and no fourth: the method, the query-free path, and the matched
#: endpoint - ``None`` on an unmatched URL, where ``%r`` renders it as ``None``
#: rather than failing.  The query string is excluded deliberately and must
#: stay excluded: it is caller-supplied text that can carry a token, a
#: credential or personal data, and copying it into a record would persist that
#: secret wherever the record goes, including a CI log (CWE-532).  Nothing in
#: the viewer reads a query parameter, so the omission costs no diagnosis.
#: Every value is formatted with ``%r`` - the method included, since Werkzeug
#: does not sanitize ``REQUEST_METHOD`` - so that a control character in a
#: crafted request cannot forge a second log line.
_NOT_FOUND_LOG_MESSAGE: Final[str] = "Answering %r %r (endpoint %r) with status 404"

#: Log line for the internal error, carrying the traceback via ``exc_info``.
#: The message names the method, the query-free path and the matched endpoint,
#: which is enough for an operator to correlate the record with an access log,
#: and withholds the query string for the same reason the not-found line above
#: withholds it: a secret a caller put in a query must not be persisted in a
#: log.  All three values are formatted with ``%r``, the method included.
_INTERNAL_ERROR_LOG_MESSAGE: Final[str] = (
    "Unhandled exception while serving %r %r (endpoint %r); answering with "
    "status 500"
)

_RENDER_FAILURE_LOG_MESSAGE: Final[str] = (
    "Rendering the error page %r failed; answering status %d as plain text"
)


def _wants_json() -> bool:
    """Whether this request should be answered with JSON rather than HTML.

    Two inputs, in this order:

    1. The matched endpoint.  A route that answers JSON on success answers
       JSON on failure, whatever the request asked for, so an endpoint in
       :data:`JSON_ENDPOINTS` decides immediately.  An unmatched URL has no
       endpoint and falls through.
    2. The ``Accept`` header, weighed strictly.  JSON wins only when it is
       preferred *above* HTML, so a missing header and ``*/*`` - which score
       the two equally - both yield HTML.  That is what the test client and a
       browser respectively send, and answering either with a JSON body would
       be wrong.

    Returns:
        ``True`` to answer with JSON, ``False`` to answer with HTML.

    """
    if request.endpoint in JSON_ENDPOINTS:
        return True

    accepted = request.accept_mimetypes
    if not accepted:
        return False

    best = accepted.best_match((_JSON_MIMETYPE, _HTML_MIMETYPE))
    return (
        best == _JSON_MIMETYPE
        and accepted[_JSON_MIMETYPE] > accepted[_HTML_MIMETYPE]
    )


def _no_store(response: Response) -> Response:
    """Apply the no-store policy to one error body.

    Every response this module answers with goes through here, so the policy
    holds for all three shapes - JSON, the rendered page, and the plain-text
    fallback a failed render produces - and cannot be forgotten by one of
    them.

    Args:
        response: The response about to be returned to the framework.

    Returns:
        The same response, carrying the policy.  Assigned rather than
        appended, so a value the framework may already have set is replaced
        instead of joined - a response advertising both would leave the weaker
        directive in force for a cache that read it first.

    """
    response.headers[_CACHE_CONTROL_HEADER] = _CACHE_CONTROL_POLICY
    response.headers[_PRAGMA_HEADER] = _PRAGMA_POLICY
    return response


def _json_response(status: HTTPStatus, message: str) -> Response:
    """Build the JSON error body: one message, one status, nothing more.

    Args:
        status: The HTTP status to carry, in the body and on the response.
        message: The cause-neutral message for this status.

    Returns:
        A JSON response whose status code and ``status`` member agree,
        carrying the no-store policy.

    """
    response = jsonify(error=message, status=int(status))
    response.status_code = int(status)
    return _no_store(response)


def _plain_response(status: HTTPStatus, message: str) -> Response:
    """Build the last-resort body, used only when a page will not render.

    Args:
        status: The HTTP status to carry - the same one the page would have.
        message: The cause-neutral message for this status.

    Returns:
        A ``text/plain`` response disclosing nothing about why the page
        failed, carrying the no-store policy.

    """
    return _no_store(
        Response(
            f"{message}\n",
            status=int(status),
            mimetype=_TEXT_MIMETYPE,
        )
    )


def _html_response(template: str, status: HTTPStatus, message: str) -> Response:
    """Render an error page with an empty context, guarding the render.

    Both pages are documented as receiving no template context whatsoever, so
    none is passed here: a variable reference in either page is a fault in the
    page and shows up as one under the strict-undefined policy the suite
    imposes, rather than being masked by a context supplied just in case.

    The render is guarded because it can fail for a reason unrelated to the
    error being reported - most plausibly an application on which the ``web``
    blueprint was never registered, where the shell's ``url_for`` calls raise.
    A handler that raised would turn a 404 into a 500, and a 500 into a bare
    framework traceback, so the failure is logged and answered with the
    plain-text equivalent at the same status.

    Args:
        template: Loader name of the page to render.
        status: The HTTP status to answer with.
        message: The cause-neutral message, used if the page will not render.

    Returns:
        The rendered page, or the plain-text equivalent at the same status.
        Either way the response carries the no-store policy.

    """
    try:
        body = render_template(template)
    # Broad on purpose: any ordinary fault in a page must degrade to the
    # plain-text body at the same status rather than escape a handler.
    # ``KeyboardInterrupt`` and ``SystemExit`` are not caught here.
    except Exception:  # noqa: BLE001,RUF100
        logger.exception(_RENDER_FAILURE_LOG_MESSAGE, template, int(status))
        return _plain_response(status, message)

    return _no_store(Response(body, status=int(status), mimetype=_HTML_MIMETYPE))


def _exception_context(error: object) -> BaseException | bool:
    """The traceback to log for an internal error, however it arrived.

    Flask wraps an unhandled exception in an ``InternalServerError`` carrying
    the original on ``original_exception``, and that original is what an
    operator needs.  An explicitly raised 500 has no original, in which case
    the exception itself carries the traceback.  Failing both, ``True`` defers
    to the active exception context, which is where the handler is called
    from.

    Args:
        error: Whatever the framework handed the handler.

    Returns:
        An exception to format, or ``True`` to use the active exception.

    """
    original = getattr(error, "original_exception", None)
    if isinstance(original, BaseException):
        return original
    if isinstance(error, BaseException) and error.__traceback__ is not None:
        return error
    return True


def _handle_not_found(_error: Exception) -> Response:
    """Answer a request the viewer cannot serve, without saying why.

    The exception is deliberately not consulted.  Ten distinct causes reach
    this handler and it must answer identically for all of them, so there is
    nothing in the exception - description, code or attached detail - that may
    reach the response.

    The DEBUG record carries the method, the path without its query string,
    and the matched endpoint, which is ``None`` for an unmatched URL.  It
    carries nothing else about the request; see
    :data:`_NOT_FOUND_LOG_MESSAGE` for why the query string is left out.

    Args:
        _error: The not-found exception, unused by requirement.

    Returns:
        The rendered not-found page, or its JSON equivalent, at status 404.

    """
    logger.debug(
        _NOT_FOUND_LOG_MESSAGE,
        request.method,
        request.path,
        request.endpoint,
    )

    if _wants_json():
        return _json_response(HTTPStatus.NOT_FOUND, NOT_FOUND_MESSAGE)
    return _html_response(NOT_FOUND_TEMPLATE, HTTPStatus.NOT_FOUND, NOT_FOUND_MESSAGE)


def _handle_internal_server_error(error: Exception) -> Response:
    """Answer a request that broke, recording the fault in the log only.

    The traceback is logged here and nowhere else visible: the response says
    that the request could not be completed and that the problem was recorded,
    and carries no exception type, message, frame or request detail.  The
    handler does not re-raise and attempts no recovery - every route is
    synchronous and read-only, so nothing is half-written for it to undo.

    The record identifies the request by method, query-free path and matched
    endpoint only.  The query string is excluded, so a secret a caller placed
    in one is not persisted alongside the traceback; see
    :data:`_INTERNAL_ERROR_LOG_MESSAGE`.

    Args:
        error: The internal-server-error exception, whose original exception
            supplies the traceback when the framework wrapped one.

    Returns:
        The rendered internal-error page, or its JSON equivalent, at status
        500.

    """
    logger.exception(
        _INTERNAL_ERROR_LOG_MESSAGE,
        request.method,
        request.path,
        request.endpoint,
        exc_info=_exception_context(error),
    )

    if _wants_json():
        return _json_response(HTTPStatus.INTERNAL_SERVER_ERROR, INTERNAL_ERROR_MESSAGE)
    return _html_response(
        INTERNAL_ERROR_TEMPLATE,
        HTTPStatus.INTERNAL_SERVER_ERROR,
        INTERNAL_ERROR_MESSAGE,
    )


def register_error_handlers(flask_app: Flask) -> None:
    """Attach the not-found and internal-error handlers to an application.

    The one entry point of this module, called from ``create_app()`` in
    ``app/__init__.py`` so that the factory stays the single place the viewer
    is wired together.  Only these two statuses are registered: every route is
    read-only and takes no input beyond two integer path segments and an
    artifact name, so the surface has no bad-request, forbidden or
    method-not-allowed condition of its own to describe.

    Registration holds no module-level state - the handlers are plain module
    functions - so a second application gets the same two functions without
    either application observing the other, and calling this twice on one
    application replaces each registration with the identical function.

    Args:
        flask_app: The application to attach the handlers to.

    Returns:
        ``None``.  The handlers are attached as a side effect; nothing is
        returned for a caller to hold or to unregister.

    """
    flask_app.register_error_handler(HTTPStatus.NOT_FOUND, _handle_not_found)
    flask_app.register_error_handler(
        HTTPStatus.INTERNAL_SERVER_ERROR, _handle_internal_server_error
    )
