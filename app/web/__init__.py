"""Root-mounted ``web_bp`` blueprint: the server-rendered report surface.

This package publishes exactly one symbol, the Flask blueprint ``web_bp``, which
``app.create_app()`` imports and registers with **no** ``url_prefix`` so that its
rules sit at the root of the URL space.

Source binding: the report sections of the README, ``[README.md:L152-L161]`` --
``### Jenkins Cucumber Reports`` (L152), ``##### HTML Report:`` (L155) and
``##### Txt Report:`` (L159) -- together with the reporting prose at
``[README.md:L42-L43]``, which records that the project produces JSON, HTML and
Txt reports plus screen shots and error shots. In the source system those
artifacts were reachable only from a Jenkins job; this blueprint makes them
browsable over HTTP without introducing any new capability.

Routes owned here (feature F-009), both defined in :mod:`app.web.routes`:
``GET /``, the landing page, and ``GET /reports``, the index over the generated
artifacts. Nothing else belongs to this blueprint. The one additive
liveness-probe route in the system is registered directly on the application
inside ``create_app()``, not here, and every ``/api/v1`` rule belongs to
:mod:`app.api`.

No error handler is registered on this blueprint, deliberately: a blueprint does
not own a URL space, so a blueprint-scoped 404 or 405 handler never fires for an
unmatched URL. Every handler lives in :mod:`app.errors` at application scope,
which is also what keeps the rendered error pages from shadowing the two real
rules above.

Layering: ``web`` sits at the same architectural layer as ``api`` and depends
downwards on ``services`` through :mod:`app.web.routes`. It must never import
``app.api``, and nothing under ``app/`` may import from ``tests/``.
"""

from flask import Blueprint

# The single name this package publishes. ``app.create_app()`` performs
# ``from app.web import web_bp``, so the identifier is part of the contract and
# must not be renamed.
__all__ = ["web_bp"]

# ``"web"`` is both the ``url_for`` namespace (``web.<endpoint>``) and the key
# under which the factory records this blueprint, so it is deliberately stable.
# No ``url_prefix`` is passed: the blueprint is mounted at the root, which is
# what places its rules at ``/`` and ``/reports``. ``template_folder`` and
# ``static_folder`` are likewise omitted so that template and static lookups keep
# resolving against the application-level ``app/templates`` and ``app/static``
# roots, where ``index.html``, ``reports.html`` and ``base.html`` live.
web_bp: Blueprint = Blueprint("web", __name__)

# Bottom import -- REQUIRED, not optional, and deliberately placed last.
# Importing ``app.web.routes`` is what attaches this blueprint's two view
# functions (``GET /`` and ``GET /reports``) to ``web_bp``, because the factory
# registers the blueprint by importing THIS package. The statement has to follow
# the assignment above: ``routes`` executes ``from app.web import web_bp``, which
# reads that name back off this partially initialised module via ``sys.modules``.
# Moving it to the top of the file, or deleting it as an "unused" import, would
# register a blueprint carrying zero rules and silently remove both routes.
#
# Spelled RELATIVELY -- ``from . import routes`` -- rather than
# ``from app.web import routes``, and that difference is load bearing for the
# type-check gate rather than cosmetic. This module declares ``__all__``, so the
# strict checker treats every name absent from it as not exported and rejects
# reading ``routes`` back off ``app.web`` ("Module \"app.web\" does not
# explicitly export attribute \"routes\""). A relative submodule import asks
# nothing of ``__all__``, keeps ``make typecheck`` clean, and has identical
# runtime effect: Python binds ``routes`` onto this package as a side effect of
# importing it, so ``app.web.routes`` still resolves for anyone who needs it.
# Do not "tidy" it back into the absolute ``from app.web import ...`` form.
from . import routes as routes  # noqa: E402,F401
