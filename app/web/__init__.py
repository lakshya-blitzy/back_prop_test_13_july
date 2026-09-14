"""Read-only artifact-viewer HTTP surface for the Testinium-QA Python port.

This package is the whole of the port's web tier: it presents, over HTTP and
strictly read-only, the report artifacts a test run has already produced. It
starts no run and writes nothing, a property that holds structurally rather
than by convention, since the package imports no service, no report writer and
no browser-automation module.

The tier has no counterpart in the Java implementation this project ports --
that implementation exposes no HTTP surface at all -- so it is recorded as
deviation 12 in the technical specification's deviation inventory (section
0.1.3) and authorized by Conflict 3 of that section: the request mandates a
Flask application while the specification states the system has no traditional
application UI, and the conflict resolves by holding Flask to the minimum it
compels, a viewer over output that already exists. Nothing here is preserved
behaviour.

Specification section 0.3.3 fixes a single blueprint for the entire surface,
defined here as :data:`web_bp`; the six view functions bound to it live in the
sibling ``routes`` module and none is defined here. The blueprint's name string
``"web"`` is a contract rather than a preference: the viewer templates and the
views address their endpoints as ``web.index``, ``web.reports_overview``,
``web.report_feature``, ``web.report_scenario``, ``web.reports_summary`` and
``web.artifact``, so renaming it would break every one of those references at
render time. Three constructor arguments are deliberately left unset:
``url_prefix``, because the six rules carry absolute paths any prefix would
corrupt, and ``template_folder`` and ``static_folder``, because template and
static lookup must resolve against the application package's own directories --
a blueprint-local folder would shadow that root and the views could no longer
render the shared partials.

Importing this package binds the six views to the blueprint, by calling
``register_routes()`` from the ``routes`` module with the blueprint constructed
below. That module imports nothing from this package, so the wiring runs in one
direction and either module may be imported first. Registering the blueprint on
an *application* is the other act and is the sole responsibility of the
``create_app()`` factory, so importing this package has no effect on any
application object; it merely makes a fully routed blueprint available for the
factory to register exactly once.
"""

__all__ = ["web_bp", "bp"]  # noqa: RUF022

from flask import Blueprint

from .routes import register_routes

#: The port's single blueprint, carrying every read-only viewer route.
#:
#: The name string is ``"web"`` because the templates and views address
#: their endpoints under that prefix. ``__name__`` is passed as the import
#: name so Flask resolves the blueprint's root path from this package. No
#: ``url_prefix``, ``template_folder`` or ``static_folder`` is supplied,
#: for the reasons given in the module docstring; each therefore stays
#: ``None`` and lookup falls back to the application-level defaults.
web_bp: Blueprint = Blueprint("web", __name__)

#: Alias for :data:`web_bp`, bound to the very same object rather than
#: to a second blueprint. It exists so that the application factory
#: resolves whichever of the two conventional spellings it imports;
#: registering either name registers the one blueprint.
bp: Blueprint = web_bp

# The six views are bound here, after the blueprint exists and before any
# caller can see it, so a fully routed blueprint is the only thing this package
# ever exports. The call is the whole of the wiring: ``routes`` holds the views
# as plain functions and imports nothing from this package, so the edge runs
# one way and the order of the statements in either file carries no meaning.
# Module caching makes this exactly one call per interpreter.
register_routes(web_bp)
