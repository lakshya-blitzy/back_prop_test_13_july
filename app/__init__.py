"""The ``create_app()`` application factory, and the ``app`` package initialiser.

Being both at once is the fact that constrains everything below, so read the
next section before editing this file.  Its single unusual property is
deliberate, load-bearing, and the thing a later editor is most likely to
"tidy up".

Why every import in this file is deferred
=========================================
This module wears two hats that pull in opposite directions.  As an
*application factory* it wants to import Flask, the blueprint, the error
handlers and the command-line surface.  As the *package initialiser* for ``app``
it must import as close to nothing as possible, because **Python cannot import
a submodule without first executing its parent package's ``__init__``**.  That
is to say: ``import app.utils.paths`` runs this file, in full, first.

The specification's section 0.4.2 states the invariant that would break::

    "app/utils imports nothing from the package, so paths.py is importable in
     a worker that never builds a Flask application."

``app/utils/paths.py`` is the port's sole owner of every artifact path.  It is
read by ``app/services/test_run_service.py`` when it computes each worker's
``-o <path>`` under the per-worker intermediates directory, and by all four
report writers.  Those worker processes are spawned to run Gherkin scenarios;
they have no HTTP surface, serve no request and build no application.

Hoisting the imports below to module level breaks two things at once:

1. **The invariant.**  Every worker process that merely wants
   ``app.utils.paths`` would drag in Flask, the blueprint, all six view
   functions, the Click command, the services layer and the reporting layer -
   for nothing.
2. **A genuine circular import.**  ``app/cli.py`` imports ``app.services`` at
   module level, which imports ``app.reporting``, which imports ``app.utils``.
   A module-level ``from app.cli import run_tests`` here would therefore start
   that chain while *this* module is still executing, so each link would be
   resolving against a half-initialised ``app`` - precisely the shape that
   raises ``ImportError: cannot import name ... (most likely due to a circular
   import)``.

Hence the rule, which is not negotiable: **at module level this file imports
only ``__future__`` annotations and ``typing.TYPE_CHECKING``; everything else
is imported inside the body of :func:`create_app`.**  The two names needed for
the signature's annotations are imported under ``TYPE_CHECKING``, so a type
checker sees them and the interpreter never does - ``from __future__ import
annotations`` keeps the annotations unevaluated at runtime.

Provenance and scope
====================
There is no Java counterpart.  Specification 0.4.1 maps this file as *"No
source: the Java project has no application factory. Sole registration point
for the blueprint and the CLI"*, and 0.3.3 records the pattern's purpose:
Flask's documented approach, and what lets the unit suite build an isolated
application.

The whole HTTP surface is an addition rather than preserved behaviour -
deviation 12 of the specification's inventory, authorized by its Conflict 3.
The request mandates a Flask application while the specification states the
system has no traditional application UI; the conflict resolves by holding
Flask *"to the minimum it compels"* - a read-only viewer over the artifacts a
run already produces.  A very small factory is therefore the correct outcome
here, not an unfinished one.

Sole registration point
=======================
Three things are wired to an application, and all three are wired here and
nowhere else:

* the single blueprint from ``app/web`` - one blueprint for the whole surface,
  because it is small and read-only, with the one JSON route sitting alongside
  the HTML routes since both read the same artifact (specification 0.3.3);
* the not-found and internal-error handlers from ``app/errors.py``;
* the ``run-tests`` command from ``app/cli.py``.

Nothing else registers any of them, and this module registers nothing else.

What this file must never acquire
=================================
No route - all six belong to ``app/web/routes.py``.  No read of
``configuration.properties`` - that file is read only by
``app/utils/properties.py``, reached only through ``app/config.py``.  No path
literal - ``app/utils/paths.py`` owns every path in the port, and a factory
that needs one is a factory holding logic that belongs elsewhere.  No
directory creation: output directories are made by a run, never by the viewer.
No triggering, scheduling or starting of a test run, in any form.  No
``selenium``, ``behave``, ``app.pages``, ``app.automation`` or ``app.reporting``
import.  No production-server surface - no WSGI middleware, no proxy fixer, no
container hook.  No Flask extension.  No second blueprint.  No module-level
mutable state, so that one application can never influence another.

Beware one name collision
=========================
Within this package ``app.config`` is the port's own six-key configuration
module, while ``flask_app.config`` is Flask's config mapping.  They are
unrelated, which is why the local application object below is named
``flask_app`` rather than ``app``.  None of the six ``configuration.properties``
keys is ever copied into Flask's config: browser, URLs, credentials and the
expected page title belong to scenario execution, and a read-only viewer has
no business with them.

Acceptance criteria
===================
Stated here so that ``tests/test_app_factory.py``, which owns the assertions,
implements them faithfully:

1. **Deferred-import proof.**  In a fresh interpreter, ``import
   app.utils.paths`` succeeds and ``"flask" not in sys.modules`` afterwards.
   This is the most important assertion in this module's suite: it is what
   protects the worker-process import path.
2. **No import-time side effects.**  ``import app`` builds no application,
   creates no directory and no file, and reads no configuration file; in
   particular the build output directory does not come into existence.
3. **A usable application.**  ``create_app()`` returns a ``Flask`` instance
   with the blueprint registered, and all six rules of specification 0.3.1
   appear in ``url_map``: ``/``, ``/reports``,
   ``/reports/features/<int:findex>``,
   ``/reports/features/<int:findex>/scenarios/<int:sindex>``,
   ``/reports/summary`` and ``/artifacts/<path:name>``.
4. **Error handlers registered.**  Handlers for 404 and 500 are present on the
   returned application.
5. **Command registered, by identity.**  ``run-tests`` is on the application's
   CLI group and *is the same object* ``app/cli.py`` defines - an identity
   check, not a name match.
6. **Isolation.**  Two applications built in one interpreter with different
   overrides share no state: the second does not see the first's overrides, and
   neither accumulates a duplicate blueprint, handler or command.
7. **Overrides.**  ``create_app({"TESTING": True})`` yields
   ``config["TESTING"] is True``, and ``create_app()`` with no argument does
   not raise.
8. **Template and static resolution.**  The application resolves
   ``app/templates`` and ``app/static`` with no explicit override - for
   instance by rendering the not-found page through the test client and by
   requesting a known static file.
9. **Works with nothing generated.**  With no build output directory at all -
   the default state, since it is ignored by version control - ``GET /``
   answers **200**.  Specification 0.3.1 makes the index the one route the
   data-availability rule does not govern: it is *"200 always, including before
   any run."*
10. **No forbidden surface.**  The file carries no route decorator, no
    ``configuration.properties`` reference, no output-directory path literal,
    no ``selenium`` or ``behave`` import, no production-server import, no
    second ``register_blueprint`` call and no Flask extension.  Assert it over
    the parsed module rather than by substring search: the prose above names
    each prohibited thing precisely in order to prohibit it, so a plain grep
    matches this docstring.
11. **Coverage.**  This module sits outside the four gated packages, so no
    numeric threshold applies; both the overrides-present and overrides-absent
    branches are still covered, along with the rejection of a non-mapping
    argument.
"""

from __future__ import annotations

# The only module-level import, and it costs nothing at runtime: TYPE_CHECKING
# is False when the interpreter runs, so the block below never executes and
# neither Flask nor collections.abc is loaded by importing this package.  See
# "Why every import in this file is deferred" above before adding a second one.
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - evaluated by type checkers, never at runtime
    from collections.abc import Mapping

    from flask import Flask

#: The published surface: one factory, and nothing else.  No application
#: object is exposed at module level - creating one at import time is exactly
#: the side effect this package must not have, and it would defeat the
#: isolation the unit suite depends on.  ``wsgi.py`` and ``run.py`` each build
#: their own from this factory.
__all__ = ["create_app"]


def create_app(config_overrides: Mapping[str, object] | None = None) -> Flask:
    """Build and return a fully wired read-only artifact-viewer application.

    The factory is free of import-time side effects and may be called any
    number of times in one interpreter: it holds no module-level state, so
    applications it returns cannot influence one another.  That is what lets
    the unit suite build an isolated application per test, and it is Flask's
    own documented Application Setup guidance.

    :param config_overrides:
        Optional mapping merged into the returned application's Flask config -
        for example ``{"TESTING": True}`` from a test, following Flask's
        upper-case key convention.  This is Flask's config only; the port's own
        six ``configuration.properties`` keys are never routed through here.
        ``None`` - the default - applies nothing and leaves Flask's defaults
        untouched.
    :returns:
        A ``Flask`` application with the single ``web`` blueprint, the two
        error handlers and the ``run-tests`` command registered.
    :raises TypeError:
        If ``config_overrides`` is given but is not a mapping.  Rejected before
        anything is constructed, so a mistyped call cannot yield a half-built
        application.
    """
    # Every import below is deliberately inside the function body.  Moving any
    # of them to module level breaks the worker-process import path and
    # introduces a circular import; the module docstring explains both in full.
    # The suppressions say the same thing to a linter: a tool that flags a
    # deferred import is, in this one file, asking for the defect.
    import logging  # noqa: PLC0415,RUF100
    from collections.abc import Mapping  # noqa: PLC0415,RUF100

    from flask import Flask  # noqa: PLC0415,RUF100

    from app.cli import run_tests  # noqa: PLC0415,RUF100
    from app.errors import register_error_handlers  # noqa: PLC0415,RUF100
    from app.logging_config import configure_logging  # noqa: PLC0415,RUF100
    from app.web import web_bp  # noqa: PLC0415,RUF100

    # Validate first and construct nothing until it passes.  A caller that
    # passes a sequence of pairs, a namespace object or a JSON string gets a
    # message naming what arrived, rather than an obscure failure from deep
    # inside Flask's config mapping several statements later.
    if config_overrides is not None and not isinstance(config_overrides, Mapping):
        raise TypeError(
            "create_app() config_overrides must be a mapping of Flask "
            "configuration keys to values, or None; got "
            f"{type(config_overrides).__name__}."
        )

    # ``Flask(__name__)`` - with ``__name__`` being ``"app"`` - resolves the
    # root path to this package's directory, so templates come from
    # app/templates and static files from app/static.  That is exactly the
    # layout specification 0.3.1 fixes, so neither ``template_folder`` nor
    # ``static_folder`` is overridden and neither is computed as an absolute
    # path: the package-relative defaults are what keeps an *installed* copy
    # working, both trees being shipped as package data by pyproject.toml.
    flask_app = Flask(__name__)

    # Applied before anything reads the config, so a value under test is in
    # place for every registration that follows.
    if config_overrides is not None:
        flask_app.config.update(config_overrides)

    # Console logging for the viewer.  Idempotent by contract - it replaces the
    # handlers it owns rather than stacking new ones - so building several
    # applications in one interpreter does not multiply output.  This factory
    # and the ``run-tests`` command are the only two callers in the port;
    # every other module acquires its logger straight from the standard
    # library, which is what keeps app/utils free of any app-package import.
    configure_logging()

    # The one blueprint, carrying all six read-only routes.  Importing
    # ``app.web`` above bound its view functions to it, so registering the
    # blueprint here registers the whole surface.  There is no second
    # blueprint and no ``url_prefix``: the six rules carry absolute paths.
    flask_app.register_blueprint(web_bp)

    # The 404 and 500 handlers.  Registered after the blueprint so that the
    # error pages, which build their links with ``url_for``, have endpoints to
    # resolve against for the whole life of the application.
    register_error_handlers(flask_app)

    # The ``run-tests`` command, attached as the *same object* app/cli.py
    # defines - not redefined, not wrapped - so the graph edge that makes this
    # module the sole registration point for the command-line surface holds.
    #
    # This path exists to satisfy that invariant and is NOT the supported way
    # to invoke a run: specification 0.4.1 states plainly that "nothing invokes
    # it through the Flask CLI".  The supported route is the ``run-tests``
    # console script declared in pyproject.toml's [project.scripts], which the
    # runner scripts, the Makefile and the README all use.  Registration here
    # neither starts a run nor makes any route capable of starting one - the
    # HTTP surface stays read-only.
    flask_app.cli.add_command(run_tests)

    # Acquired here rather than at module level, keeping this file's only
    # module-level import the typing one.  DEBUG, because an application being
    # built is unremarkable: it stays off the console unless a caller asked
    # for verbosity, and can therefore never disturb the stdout/stderr split
    # that the command-line contract depends on.
    logging.getLogger(__name__).debug(
        "Read-only artifact viewer application created; %d override(s) applied",
        len(config_overrides) if config_overrides is not None else 0,
    )

    return flask_app
