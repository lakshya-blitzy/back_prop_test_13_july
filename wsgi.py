"""WSGI entry point for the read-only artifact viewer.

The server-loadable target is ``wsgi:app``.  Everything this module does is
below: it imports the application factory, calls it once, and publishes the
resulting callable.  There is no configuration to adjust here and nothing to
extend - a module that grows past those three statements has taken on work
that belongs to the factory or to one of the packages it wires together.

Provenance
==========
There is no Java counterpart.  The technical specification's section 0.4.1
maps this file, paired with ``run.py``, as *"No source: the Java project has no
HTTP surface.  Both obtain the application from create_app()"*.  The Maven
manifest this port replaces - retained in the tree as historical reference
only, and no longer a supported build configuration - declares a browser
test-automation suite and no server of any kind, which is why no behaviour is
carried over into this file and none needs to be.

The HTTP surface itself is an addition rather than preserved behaviour:
deviation 12 of the specification's inventory, authorized by its Conflict 3.
The request mandates a Flask application while the specification states the
system has no traditional application UI, and the conflict resolves by holding
Flask *"to the minimum it compels"* - a read-only viewer over the artifacts a
run already produces.  This module is the smallest expression of that scope,
and being three statements long is the intended outcome, not an unfinished
one.

Two names, one application
==========================
``app`` is the idiomatic Flask name and makes the target ``wsgi:app``.
``application`` is a plain alias of the same object, published because several
servers look for that name when none is given.  It is an alias and nothing
more: both names are bound to the one application the factory returned, so no
deployment can accidentally end up serving two differently wired applications
from this file.

The factory runs exactly once per interpreter.  Python caches an imported
module, so a second ``import wsgi`` returns the module already built rather
than calling the factory again, and every request a server dispatches reaches
that one application object.  Building an application per worker process is
therefore the server's decision, made by how it forks or spawns, and not
something this module either forces or prevents.

Division of labour with ``run.py``
==================================
Specification section 0.3.1 lists two entry points, and the split between them
is deliberate.  This module publishes a callable for a server to load and
starts nothing.  Its sibling ``run.py`` owns the development server, and with
it every choice about host, port, reloading and verbosity.  Duplicating that
here is the well-known way two entry points come to disagree about how the
same application behaves, so the development-server call and its main guard
appear only there.

How this callable is served is out of scope.  Specification section 0.2.2
excludes the container and production deployment surface - no image
definition, no orchestration file and no production server configuration - and
the dependency inventory of section 0.5.1 deliberately pins no WSGI server.
This module exposes the callable and stops.

What this module never acquires
===============================
The factory in the application package is the sole registration point for the
blueprint carrying all six routes, for the not-found and internal-error
handlers, and for the ``run-tests`` command; section 0.4.2 states the
invariant, naming this file among those that *"all obtain their application
from create_app()"*.  Registering any of them a second time here would give
the application a duplicate of something it already has.

So this file adds no route and no route decorator, no error handler and no
template filter; it configures no logging, since the factory already does and
the logging module owns that behaviour; it reads no properties file, which has
exactly one reader elsewhere in the port; it holds no artifact path literal,
because those paths have a single owner module and a WSGI entry point has no
business computing one; it sets no environment variable and hard-codes no
debug flag, both of which belong to the development entry point; and it
imports neither the browser-automation library nor anything from the services,
reporting, pages or automation packages, so loading this module can never
reach the layers that produce artifacts, let alone start a run.  The
read-only guarantee of the HTTP surface is upheld structurally here, by what
this file cannot reach, rather than by convention.

Verified properties
===================
Established by exercising the module rather than by inspection:

1. ``wsgi.app`` is a ``Flask`` instance and is callable, so it is a valid WSGI
   application object.
2. All six rules of specification section 0.3.1 are present in its URL map,
   alongside Flask's own static rule - proof that the factory did the wiring
   and this module did not.
3. ``wsgi.application is wsgi.app`` - one application, two names.
4. Importing the module has no side effect on the filesystem: no directory is
   created, no file is written, and neither the network nor a browser is
   touched.  In particular the generated output directory does not come into
   existence, so a fresh checkout can be served before any run has happened.
5. Through the test client, with nothing generated at all, the landing route
   answers 200 - specification section 0.3.1 makes it *"200 always, including
   before any run"* - while the report routes answer 404 under the single
   data-availability rule, the response being the same for an absent,
   unreadable or unparseable results file.
"""

from app import create_app

#: The published surface: one application object under two names, and no
#: helper, flag or hook alongside them.
__all__ = ["app", "application"]

#: The WSGI callable a server loads as ``wsgi:app``.  Built by the factory at
#: import time with no overrides, which is the default application: the
#: factory's optional mapping exists for tests that need a value such as
#: ``TESTING`` in place, and a served application wants none of them.  A
#: server that needs different behaviour changes its own configuration, not
#: this call.
app = create_app()

#: A plain alias of :data:`app`, for servers whose default callable name is
#: ``application``.  Assignment only - never a second factory call.
application = app
