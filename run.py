"""Development entry point: serve the read-only artifact viewer locally.

Invoke it with the project interpreter and no arguments::

    .venv/bin/python run.py

It serves the viewer over the artifacts a suite run has already produced, and
starts nothing else - no suite run, no browser.  ``wsgi.py`` exposes the same
factory's application for a WSGI server to load.

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

Serving defaults, and the only thing that adjusts them
======================================================
* **Host** - the loopback address, fixed.  A viewer of local build output has
  no reason to accept connections from the network, and binding every
  interface by default would publish one machine's results to its neighbours.
  An operator who genuinely wants that should reach for the WSGI counterpart
  and a real server rather than edit a value here.
* **Port** - 5000, Flask's own default, overridable through the ``FLASK_PORT``
  environment variable.  One override exists for one reason: several checkouts
  of this project may need to serve at once on a shared machine, and a fixed
  port makes that impossible.  Unset or empty means the default.
* **Accepted ``Host`` headers** - not set here at all.  The loopback bind
  above decides which interfaces a connection may arrive on; what decides
  which *names* it may claim is the ``TRUSTED_HOSTS`` allowlist the factory
  installs, and it is what makes a request arriving under a foreign name a
  400 rather than a 200.  The two work together: the bind keeps the socket
  local, and the allowlist keeps a page loaded under an attacker-controlled
  hostname from reading this machine's artifacts through the browser that
  loaded it.  So widening the bind is not a one-value change here - it means
  revising that allowlist too, through ``create_app``'s overrides, where the
  whole decision lives in one place.

Debug mode is off and is not configurable here.  ``debug=False`` is stated
outright at the call below rather than left to a framework default a later
edit could move, and no environment variable, option or file switches it on,
because Flask's debug mode serves an interactive console capable of executing
arbitrary code in this process.  A developer who wants the reloader and the
debugger reaches for ``flask run`` per command and knows they have.

The port variable is not a configuration layer, and it may not grow into one.
Specification 0.4.1 fixes the port's only application-configuration override
path as the suite runner's browser userdata, stating "this is the only override
path; no environment layer is added".  The single read below is a server
option - where to listen - and it is deliberately one direct read with a
documented default: no file, no precedence chain, no merging, no second
variable, and nothing reaching the application's own configuration.

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
   existence.  The port variable is not even consulted, because that one read
   happens inside the guard.
3. **Running does serve.**  Started as a script with no generated artifacts
   present, the index answers 200 on the configured port, and a report route
   answers 404 through the rendered error page rather than a traceback.
4. **Read-only surface.**  ``app.url_map`` carries the six documented rules and
   Flask's static rule, and no rule that starts, schedules or queues a run.
5. **Port resolution.**  Unset or empty yields the default; a whole number in
   range is honoured; a non-numeric or out-of-range value exits with a message
   naming the variable and the accepted range, not a traceback.
"""

from __future__ import annotations

import os
from typing import Final

from app import create_app

__all__ = ["app"]

DEFAULT_HOST: Final[str] = "127.0.0.1"

DEFAULT_PORT: Final[int] = 5000

PORT_VARIABLE: Final[str] = "FLASK_PORT"


def _port_from_environment() -> int:
    """Return the port to listen on, resolving the ``FLASK_PORT`` override.

    Unset or empty - an exported-but-empty variable being how a shell conveys
    "no value" - means :data:`DEFAULT_PORT`.  Anything else must be a whole
    number within the usable TCP range.

    :returns: A port number between 1 and 65535 inclusive.
    :raises SystemExit:
        If the variable holds a value that is not a usable port.  The caller
        gets a message naming the rejected value and the accepted range, rather
        than a traceback or a silent fall back to a port nobody is watching.
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
        # ``from None``: the underlying ValueError adds nothing actionable.
        raise SystemExit(
            f"{PORT_VARIABLE}={raw!r} is not a number. {guidance}"
        ) from None

    if not 1 <= port <= 65535:
        raise SystemExit(
            f"{PORT_VARIABLE}={raw!r} is not a usable TCP port. {guidance}"
        )
    return port


app = create_app()


if __name__ == "__main__":
    app.run(
        host=DEFAULT_HOST,
        port=_port_from_environment(),
        debug=False,
    )
