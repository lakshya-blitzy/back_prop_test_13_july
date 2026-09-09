"""Application-level HTTP error handlers for the read-only artifact viewer.

Two handlers, one entry point, and no capability of its own.  This module
answers a request the viewer cannot serve - status 404 - and a request that
broke while being served - status 500 - and it does nothing else.

Provenance
----------
This file has no counterpart in the Java implementation the project ports.
AAP 0.4.1 maps it accordingly: *"``app/errors.py``, ``app/logging_config.py``
| CREATE | -- | No source: handlers for the added HTTP surface"*.  The whole
HTTP surface is an addition rather than preserved behaviour - deviation 12 of
the AAP's inventory, authorized by its Conflict 3, which resolves the clash
between a request mandating a Flask application and a specification stating
the system has no traditional application UI by holding Flask *"to the minimum
it compels - a read-only viewer over the artifacts a run already produces"*.
Nothing beyond that minimum belongs here, which is why there is no error
taxonomy below: no 400, 403, 405 or 503 handler, no problem-details envelope,
no error identifier, and no monitoring hook.

The public surface
------------------
:func:`register_error_handlers` and nothing else.  It is called from
``create_app()`` in ``app/__init__.py`` and from nowhere else: AAP 0.4.2 makes
that factory the sole registration point for the blueprint and the
command-line surface, and keeping handler registration behind one function
called from the same place upholds that discipline instead of working around
it.

The direction of dependency is one-way and deliberate.  This module defines no
route, imports no blueprint, and is imported by no view.  ``app/web/routes.py``
raises or aborts; the factory wires both sides; the two never reference each
other.

One response for every cause
----------------------------
Most 404s this handler serves are not a mistyped URL.  They are the
data-availability rule AAP 0.3.1 states once for all four report routes: when
the results artifact a run writes is absent, unreadable or unparseable, the
route answers 404, and it answers *"the same response for all three causes,
because a run has not produced usable results and the distinction is not the
viewer's to make."*  That artifact is deliberately not named anywhere in this
file - ``app/utils/paths.py`` owns every artifact path in the port, and a
handler that discloses none of them has no use for one.

That collapse is the single most important behaviour in this file, and it is
counter-intuitive: a good error handler usually says which thing went wrong.
Here it must not.  The response carries no cause, no code and no diagnostic
detail, so the three causes are indistinguishable from each other and from the
remaining causes the same status covers:

* an out-of-range feature index on the feature route;
* either index out of range on the scenario route;
* on the artifact route - a name outside the allowlist, an allowlisted name
  with nothing behind it, a directory that is not the report tree, a path that
  resolves outside the artifact root, and any worker-intermediates path, which
  must never be reachable.

Ten causes, one response.  A rejected traversal or worker-intermediates path
is answered with a plain 404 rather than a 403, and the response never echoes
the rejected name and never carries a filesystem path: reflecting either would
confirm the layout to a prober and would separate a probe from an honest
mistake.  Diagnosis lives in the process log - the rejecting route logs the
rejection it detected, and this module logs a cause-neutral line of its own -
never in the body.

Byte-identity across the ten causes is a property of the rendered pages
themselves, which carry no dynamic content whatsoever, and of the messages
below, which are constants.  Nothing here consults the request in order to be
helpful, and nothing may start to.

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

The internal-error handler
--------------------------
The response says that the request could not be completed and that the problem
was recorded, and says nothing else: no exception type, no exception message,
no traceback, no frame, no request detail, no configuration value, no path, no
version and no host.  The diagnostic exists, in the log, where an operator can
read it and a caller cannot.

Logging is acquired from the standard library - ``logging.getLogger(__name__)``
- and ``app/logging_config.py`` is deliberately not imported: that module
installs handlers and is called only by the two process entry points, and
every other module in the port acquires its logger directly.  Its handler
split sends WARNING and above to the error stream, so the traceback this
module records lands there without this module knowing anything about streams.

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
9. **Coverage.**  This module sits outside the four gated packages, so no
   numeric threshold applies to it; both handlers are still covered on both
   negotiation branches.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Final

from flask import Flask, Response, jsonify, render_template, request

# The published surface is the registration function alone.  The constants
# below are documented and stable enough for a test to read, but they are not
# part of what another module may rely on: the factory calls one function.
__all__ = ["register_error_handlers"]

#: Module logger, acquired from the standard library exactly as every other
#: module in the port does.  ``app/logging_config.py`` installs the handlers
#: for the whole ``app`` hierarchy and is not imported here; before it runs,
#: the standard library's own last-resort handler still carries WARNING and
#: above to the error stream, so nothing this module logs is ever lost.
logger = logging.getLogger(__name__)

#: The not-found page.  A Jinja loader name, resolved against the application
#: package's template directory - not a filesystem path.
NOT_FOUND_TEMPLATE: Final[str] = "errors/404.html"

#: The internal-error page, under the same loader.
INTERNAL_ERROR_TEMPLATE: Final[str] = "errors/500.html"

#: Endpoints whose successful response is JSON, and whose errors therefore are
#: too.  AAP 0.3.1's route table carries exactly one: ``GET /reports/summary``.
#: Held as an endpoint-name string rather than reached through an import,
#: because this module must not import the blueprint; ``app/web/__init__.py``
#: fixes the same names and calls them a contract rather than a preference.
JSON_ENDPOINTS: Final[frozenset[str]] = frozenset({"web.reports_summary"})

#: The one thing a 404 says.  Cause-neutral by requirement: it names none of
#: the ten causes, echoes nothing the caller sent, and carries no path.  The
#: HTML wording lives in the template; this is the JSON and plain-text body,
#: and the two agree in substance.
NOT_FOUND_MESSAGE: Final[str] = (
    "Not found. This viewer renders only the report artifacts a completed "
    "test run has already written, and it has nothing to show for this "
    "request. Runs are started from the command line with run-tests."
)

#: The one thing a 500 says.  It states that a record was made, and names no
#: log, no destination and no detail, because where the record went is
#: operator knowledge rather than a caller's business.
INTERNAL_ERROR_MESSAGE: Final[str] = (
    "Internal server error. The problem has been recorded in the server log, "
    "and no details of it are reported here."
)

#: Media types the negotiation weighs, and nothing else.
_JSON_MIMETYPE: Final[str] = "application/json"
_HTML_MIMETYPE: Final[str] = "text/html"
_TEXT_MIMETYPE: Final[str] = "text/plain"

#: Log line for a routine not-found.  DEBUG rather than WARNING on purpose: a
#: 404 is the viewer's ordinary answer before a run has produced results, and
#: at WARNING every such request would be an entry on the error stream.  The
#: security-relevant detail of a rejection - which allowlist rule refused a
#: name, which path escaped the artifact root - is logged by the route that
#: detected it, since that is the only code that knows.
_NOT_FOUND_LOG_MESSAGE: Final[str] = "Answering %s %r with status 404"

#: Log line for the internal error, carrying the traceback via ``exc_info``.
#: The message names the request so an operator can correlate the record with
#: an access log, and both values are formatted with ``%r`` so that a control
#: character in a crafted request cannot forge a second log line.
_INTERNAL_ERROR_LOG_MESSAGE: Final[str] = (
    "Unhandled exception while serving %s %r; answering with status 500"
)

#: Log line for the guarded render's own failure.  Distinct from the two
#: above, so a template or URL-building fault is not mistaken for the error
#: that brought the handler here in the first place.
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
        # No Accept header at all: HTML is the viewer's default surface.
        return False

    best = accepted.best_match((_JSON_MIMETYPE, _HTML_MIMETYPE))
    return (
        best == _JSON_MIMETYPE
        and accepted[_JSON_MIMETYPE] > accepted[_HTML_MIMETYPE]
    )


def _json_response(status: HTTPStatus, message: str) -> Response:
    """Build the JSON error body: one message, one status, nothing more.

    Args:
        status: The HTTP status to carry, in the body and on the response.
        message: The cause-neutral message for this status.

    Returns:
        A JSON response whose status code and ``status`` member agree.

    """
    response = jsonify(error=message, status=int(status))
    response.status_code = int(status)
    return response


def _plain_response(status: HTTPStatus, message: str) -> Response:
    """Build the last-resort body, used only when a page will not render.

    Args:
        status: The HTTP status to carry - the same one the page would have.
        message: The cause-neutral message for this status.

    Returns:
        A ``text/plain`` response disclosing nothing about why the page failed.

    """
    return Response(
        f"{message}\n",
        status=int(status),
        mimetype=_TEXT_MIMETYPE,
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

    """
    try:
        body = render_template(template)
    # A handler must not raise, whatever the page does; see the docstring. The
    # suppression covers the deliberate breadth of the catch and, as in
    # app/web/__init__.py, its own possible unusedness under a linter
    # configuration that selects neither rule.
    except Exception:  # noqa: BLE001,RUF100
        logger.exception(_RENDER_FAILURE_LOG_MESSAGE, template, int(status))
        return _plain_response(status, message)

    return Response(body, status=int(status), mimetype=_HTML_MIMETYPE)


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

    Args:
        _error: The not-found exception, unused by requirement.

    Returns:
        The rendered not-found page, or its JSON equivalent, at status 404.

    """
    logger.debug(_NOT_FOUND_LOG_MESSAGE, request.method, request.full_path)

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
        request.full_path,
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
    ``app/__init__.py`` and from nowhere else, so that the application factory
    stays the single place the viewer is wired together.

    Only these two statuses are registered.  Every route is read-only and
    takes no input beyond two integer path segments and an artifact name, so
    the surface has no bad-request, forbidden or method-not-allowed condition
    of its own to describe, and inventing handlers for them would grow an
    error taxonomy the system does not have.

    Registration is per application and holds no module-level state: the
    handlers are plain module functions, so calling this on a second
    application registers the same two functions there without either
    application observing the other.  Calling it twice on one application is
    equally harmless - the second registration replaces the first with the
    identical function.

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
