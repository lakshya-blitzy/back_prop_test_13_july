"""Development entry point: serve the read-only artifact viewer locally.

Invoke it with the project interpreter and no arguments::

    .venv/bin/python run.py

That is the whole of this file's job: obtain an application from the factory and
start Flask's development server on the loopback interface.  Its counterpart,
``wsgi.py``, hands the same factory's product to a WSGI caller and starts
nothing.  The two differ in exactly one thing - the ``if __name__ ==
"__main__"`` guard at the bottom of this file - so the property to protect when
editing either is that *importing* this module yields an application and no
listening socket, while *running* it listens.

What is being served
====================
A read-only viewer over the artifacts a suite run has already produced, and
nothing more.  Specification 0.1.3 resolves the request for a Flask application
against a specification that excludes a web UI by holding Flask "to the minimum
it compels", and records the whole surface as deviation 12 - an addition, not
preserved behaviour.  Its six routes, all synchronous and all read-only, belong
to ``app/web/routes.py`` and are listed in specification 0.3.1: the index, the
report overview, a feature, a scenario, a JSON summary, and an allowlisted
artifact download.  The index answers 200 always, "including before any run",
so this server is useful against a checkout that has never executed anything.
The four report routes answer 404 while no usable results exist.

Nothing here, and nothing behind those routes, can start a suite run.
Specification 0.2.2 excludes "an HTTP endpoint that starts a test run.
Execution stays on the command line; the HTTP surface is read-only."  Runs are
started by the ``run-tests`` console script alone.

Serving defaults, and the only two things that adjust them
==========================================================
* **Host** - the loopback address, fixed.  A viewer of local build output has
  no reason to accept connections from the network, and binding every
  interface by default would publish one machine's results to its neighbours.
  An operator who genuinely wants that should reach for the WSGI counterpart
  and a real server rather than edit a value here.
* **Port** - 5000, Flask's own default, overridable through the ``FLASK_PORT``
  environment variable.  One override exists for one reason: several checkouts
  of this project may need to serve at once on a shared machine, and a fixed
  port makes that impossible.  Unset or empty means the default.
* **Debug** - off unless ``FLASK_DEBUG`` asks for it, using the same rule
  Flask's own command line applies to that variable.  Off is the correct
  default even for a development-only file, because debug mode serves an
  interactive console capable of executing arbitrary code in this process; a
  developer who wants the reloader and the debugger opts in per command and
  knows they have.

Neither variable is a configuration layer, and neither may grow into one.
Specification 0.4.1 fixes the port's only application-configuration override
path as the suite runner's browser userdata, stating "this is the only override
path; no environment layer is added".  Both reads below are server options -
where to listen, and whether to reload - and they are deliberately two direct
reads with documented defaults: no file, no precedence chain, no merging, and
nothing reaching the application's own configuration.

Why the interpreter is not chosen here
======================================
There is no interpreter line at the top of this file and it needs no executable
bit, so ``./run.py`` is not how it is invoked.  The project pins one exact
interpreter - see ``.python-version`` and the ``requires-python`` constraint in
``pyproject.toml`` - and specification 0.3.1 requires the runner scripts to
"fail with a clear message rather than falling back to whatever ``python3``
resolves to".  An interpreter line invites precisely that fallback.  Naming the
interpreter at the call site keeps the choice explicit and visible.

What this file must never acquire
=================================
The factory in ``app/__init__.py`` is the sole registration point for the
blueprint, the error handlers and the command-line surface, and specification
0.4.2 names this file among those that "obtain their application from
``create_app()``".  So: no route decorator, no blueprint registration, no URL
rule added by hand, no error handler, no logging setup, and no registration or
invocation of the suite runner - a console script whose entry point is declared
in ``pyproject.toml``, and which specification 0.4.1 says nothing invokes
through the Flask command line.  No read of the port's own six-key properties
file, which has exactly one reader.  No path literal for generated output,
which has exactly one owner in ``app/utils/paths.py``.  No browser-automation
import, which is confined to one package.  No import from the services,
reporting, pages or automation packages.  No production-server surface: the
development server is the right answer here and its own startup warning says
what it is.

The overlap with ``wsgi.py`` is the factory call and nothing else.  Anything
else appearing in both files is a duplication to remove, not a convention.

Acceptance criteria
===================
Stated here so that whichever test module covers this file implements them
faithfully:

1. **Import does not serve.**  ``import run`` returns promptly, exposes ``app``
   as a ``Flask`` instance, and leaves no socket listening.  A hang means the
   server call escaped the guard.
2. **Import has no other side effect.**  No directory is created, no file is
   written, no configuration file is read and no network or browser access is
   attempted; in particular the generated-output directory does not come into
   existence.  The port and debug variables are not even consulted, because
   both reads happen inside the guard.
3. **Running does serve.**  Started as a script with no generated artifacts
   present, the index answers 200 on the configured port, and a report route
   answers 404 through the rendered error page rather than a traceback.
4. **Read-only surface.**  ``app.url_map`` carries the six documented rules and
   Flask's static rule, and no rule that starts, schedules or queues a run.
5. **Port resolution.**  Unset or empty yields the default; a whole number in
   range is honoured; a non-numeric or out-of-range value exits with a message
   naming the variable and the accepted range, not a traceback.
6. **Debug resolution.**  Unset, empty, ``0``, ``false`` or ``no`` yields
   ``False``; any other non-empty value yields ``True``.
"""

from __future__ import annotations

import os
from typing import Final

from app import create_app

#: The published surface: the application object, which is what a WSGI-style
#: import of this module is for.  The two helpers below are private because
#: they answer one question each for the guard at the bottom and are of no use
#: to anything else.
__all__ = ["app"]

#: The loopback interface, and not a configurable one.  See the module
#: docstring: publishing local build output to the network is a decision for a
#: real server, not a default for a development runner.
DEFAULT_HOST: Final[str] = "127.0.0.1"

#: Flask's own default port, and therefore the one a reader expects without
#: being told.  It is the single fact the project's documentation needs to
#: quote about this file, so it is declared once, here, rather than repeated in
#: a string below.
DEFAULT_PORT: Final[int] = 5000

#: Chooses the listening port when several checkouts must serve at once.
PORT_VARIABLE: Final[str] = "FLASK_PORT"

#: Opts into the reloader and the interactive debugger.  Flask's own command
#: line reads the same variable, and :func:`_debug_from_environment` applies the
#: same rule to it.
DEBUG_VARIABLE: Final[str] = "FLASK_DEBUG"


def _port_from_environment() -> int:
    """Return the port to listen on, resolving the environment override.

    Unset - and empty, since an exported-but-empty variable is how a shell
    conveys "no value" - means :data:`DEFAULT_PORT`.  Anything else must be a
    whole number within the usable TCP range.

    :returns: A port number between 1 and 65535 inclusive.
    :raises SystemExit:
        If the variable holds a value that is not a usable port.  Exiting with
        a message is the right failure for a script entry point: the operator
        set the variable seconds ago and needs to be told which value was
        rejected and what would be accepted, not shown a stack trace.  Silently
        falling back to the default would be worse still, because the server
        would then listen somewhere the operator is not looking.
    """
    raw = os.environ.get(PORT_VARIABLE)
    if raw is None or not raw.strip():
        return DEFAULT_PORT

    guidance = (
        f"Set {PORT_VARIABLE} to a whole number between 1 and 65535, "
        f"or leave it unset to use the default of {DEFAULT_PORT}."
    )
    try:
        port = int(raw.strip())
    except ValueError:
        # ``from None`` keeps the message clean: the underlying ValueError adds
        # nothing an operator can act on.
        raise SystemExit(
            f"{PORT_VARIABLE}={raw!r} is not a number. {guidance}"
        ) from None

    if not 1 <= port <= 65535:
        raise SystemExit(
            f"{PORT_VARIABLE}={raw!r} is not a usable TCP port. {guidance}"
        )
    return port


def _debug_from_environment() -> bool:
    """Return whether to serve in debug mode, resolving the opt-in variable.

    The rule is Flask's own, reproduced rather than imported so that this file
    depends on nothing beyond the factory: false when the variable is unset,
    empty, ``0``, ``false`` or ``no`` in any casing, and true for any other
    non-empty value.  Matching Flask means a developer who already exports the
    variable for ``flask run`` gets the same behaviour here, with no second
    convention to learn.

    :returns: ``True`` to enable the reloader and the interactive debugger.
    """
    value = os.environ.get(DEBUG_VARIABLE)
    return bool(value and value.lower() not in {"0", "false", "no"})


#: The application, built by the factory - the one action this module performs
#: at import time, and the only line it has in common with ``wsgi.py``.  The
#: factory holds no module-level state, so building one here cannot disturb
#: another built elsewhere in the same interpreter.
#:
#: This name shadows the imported ``app`` package inside this module only.
#: That is the established Flask idiom and it is safe here because nothing
#: below refers to the package again; the factory is already bound above.
app = create_app()


if __name__ == "__main__":
    # The guard is this file's reason to exist, and the line that separates it
    # from ``wsgi.py``: without it, importing the module would start a server
    # and every importer - a test, a shell, a documentation tool - would hang.
    #
    # Both environment reads sit inside it deliberately, so that an import
    # neither consults nor validates them, and a mistyped override can only
    # ever fail the person who typed it.
    #
    # Flask's development server announces what it is on startup, which is the
    # whole of the "this is not production" messaging this file needs.
    app.run(
        host=DEFAULT_HOST,
        port=_port_from_environment(),
        debug=_debug_from_environment(),
    )
