"""Read-only artifact-viewer HTTP surface for the Testinium-QA Python port.

This package is the whole of the port's web tier. It presents, over HTTP and
strictly read-only, the report artifacts that a test run has already produced.
It never starts a run, never writes to disk, and never reaches into the layers
that produce those artifacts -- a property enforced structurally rather than by
convention, since this package imports no service, no report writer and no
browser-automation module.

Provenance
----------
The web tier has no counterpart in the Java implementation this project ports:
that implementation exposes no HTTP surface at all, as its build manifest and
documentation both confirm. The package is therefore recorded as deviation 12
in the technical specification's deviation inventory (section 0.1.3), and it is
authorized by Conflict 3 of that section -- the request mandates a Flask
application, while the specification states the system has no traditional
application UI. The conflict resolves by holding Flask to the minimum it
compels: a viewer over output that already exists, and nothing beyond it.
Nothing in this package is preserved behaviour; all of it is a recorded
addition.

Structure
---------
Specification section 0.3.3 fixes a single blueprint for the entire surface:
the surface is small and read-only, so the one JSON route sits alongside the
HTML routes because both read the same artifact. That blueprint is defined
here, as :data:`web_bp`. The six view functions bound to it live in the sibling
``routes`` module; none is defined here.

The blueprint's name string ``"web"`` is a contract rather than a preference.
The viewer templates and the views themselves address their endpoints as
``web.index``, ``web.reports_overview``, ``web.report_feature``,
``web.report_scenario``, ``web.reports_summary`` and ``web.artifact``.
Renaming the blueprint would break every one of those references at render
time, so the name is fixed.

Three constructor arguments are deliberately left unset:

``url_prefix``
    The six rules carry absolute paths, which any prefix would corrupt.
``template_folder``
    Template lookup must resolve against the application package's own
    template directory, so the views can render the shared partials; a
    blueprint-local folder would shadow that root.
``static_folder``
    Static lookup must likewise stay at application level, so that the
    endpoint the base template references keeps resolving.

Registration
------------
Two different acts, and this module performs exactly one of them.

Binding the six views to the blueprint happens here, once, by calling
``register_routes()`` from the sibling ``routes`` module with the blueprint
constructed below. The wiring runs in one direction only: this package imports
that module, and that module imports nothing from this package, so neither
import depends on the order the statements in either file happen to be written
in and either module may be imported first.

Registering the blueprint on an *application* is the other act, and it is the
sole responsibility of the ``create_app()`` factory in the application package
-- which is also the only place the command-line surface and the
application-level error handlers are wired. Importing this package therefore
has no effect on any application object; it merely makes the blueprint, with
its six routes already bound, available for the factory to register exactly
once.
"""

# Ordered canonical name first, alias second, rather than alphabetically.
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
