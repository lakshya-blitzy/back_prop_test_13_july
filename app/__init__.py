"""The ``create_app()`` application factory, and the ``app`` package initialiser.

Being both at once is what shapes this file.  As a factory it wants Flask, the
blueprint, the error handlers and the command-line surface; as the ``app``
package initialiser it must import as close to nothing as possible, because
``import app.utils.paths`` runs this file in full first - including in a
worker that runs scenarios, builds no application and serves no request, the
case AAP 0.4.2 requires ``app/utils`` to stay importable in.

The import rule
===============
At module level this file executes only ``__future__`` annotations and
``typing.TYPE_CHECKING``; the two annotation-only names sit under that guard,
which the interpreter never evaluates.  ``logging``, ``collections.abc``,
``flask``, ``app.cli``, ``app.errors``, ``app.logging_config`` and ``app.web``
are imported inside :func:`create_app` for two reasons: an ``import
app.<anything>`` has to stay cheap for a worker that never builds an
application, and ``app.cli`` imports ``app.services`` -> ``app.reporting`` ->
``app.utils`` at module level, so acquiring it here would resolve that chain
against a half-initialised ``app`` and raise ``ImportError``.

The factory must never acquire ``app.pages``, ``app.automation``,
``app.reporting``, a production-server surface - WSGI middleware, a proxy
fixer, a container hook - or any Flask extension: none of them serves a
read-only viewer, which is all Flask is held to here (AAP deviation 12, under
its Conflict 3).  ``tests/test_app_factory.py`` asserts that over the parsed
module, because the prose here names each prohibited thing in order to
prohibit it and a substring search would match the prose itself.

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
12. **Host validation.**  A factory-built application carries the local
    ``TRUSTED_HOSTS`` allowlist, answers **200** for each local spelling a
    browser or a curl might send - the bare name, the bare address, either
    with a port, and the bracketed IPv6 loopback - and answers **400**, not
    200, for a foreign name with or without a port.  The allowlist is exact
    for a bracketed IPv6 literal too, which the framework's own comparison is
    not: ``[::1]`` is answered while ``[::2]``, a global or documentation-range
    literal, the IPv4-mapped form, an uncompressed spelling of the loopback
    and a bracketed value that is not a host at all are all refused, before
    any view function runs.  An explicit ``TRUSTED_HOSTS`` override replaces
    the default rather than merging with it, so the overriding name is
    accepted *and* the local ones are refused - including when the override
    names an IPv6 literal of its own - while an allowlist naming nothing
    restricts nothing, which is the framework's documented default.
"""

from __future__ import annotations

# The only module-level import, and it costs nothing at runtime: TYPE_CHECKING
# is False when the interpreter runs, so the block below never executes and
# neither Flask nor collections.abc is loaded by importing this package.  See
# "Why every import in this file is deferred" above before adding a second one.
# ``Final`` comes from the same module, so widening this import adds no second
# module to the set an ``import app.<anything>`` executes.
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # pragma: no cover - evaluated by type checkers, never at runtime
    from collections.abc import Mapping

    from flask import Flask

__all__ = ["create_app"]

#: The ``Host`` header values a factory-built application answers for: the
#: loopback address, its name, and the IPv6 loopback in the bracketed form a
#: browser sends.  Private, because the published surface is the factory alone
#: and a caller that needs a different set says so through
#: :func:`create_app`'s overrides rather than by reaching in here.
#:
#: The three names are exactly the ones a local viewer is reached by.  Flask
#: accepts every ``Host`` by default, which for this port is a
#: Host-header/DNS-rebinding hole rather than a convenience: the six routes
#: carry no authentication because the security model is a loopback-bound
#: read-only viewer, and the artifacts they expose carry the suite's
#: credentials, its failure screenshots and its tracebacks.  A page a browser
#: was tricked into loading under an attacker-controlled name must therefore
#: not be able to read them (CWE-346).
_TRUSTED_HOSTS: Final[tuple[str, ...]] = ("127.0.0.1", "localhost", "[::1]")

#: The Flask configuration key holding the allowlist, and the request
#: environment key holding the value to judge against it.  The environment is
#: read rather than ``request.host`` because what has to be validated is the
#: header exactly as it arrived, before any normalisation of it.
_TRUSTED_HOSTS_KEY: Final[str] = "TRUSTED_HOSTS"
_HOST_ENVIRON_KEY: Final[str] = "HTTP_HOST"

#: The three characters that separate a ``Host`` header's parts.  A bracketed
#: IPv6 literal ends at its closing bracket and only what follows that can be
#: a port, which is precisely the distinction the framework's own comparison
#: does not make.
_IPV6_OPEN: Final[str] = "["
_IPV6_CLOSE: Final[str] = "]"
_PORT_SEPARATOR: Final[str] = ":"

#: What an untrusted ``Host`` is answered with.  The same status the framework
#: answers a rejected name with, so the two halves of the check are
#: indistinguishable to a caller and neither discloses which one refused.
_UNTRUSTED_HOST_STATUS: Final[int] = 400


def _host_without_port(value: object) -> str:
    """Return one ``Host`` value's host part, lowercased, or the empty string.

    The parse the allowlist needs and the framework's own comparison does not
    perform.  Werkzeug's ``host_is_trusted`` splits a host - and every trusted
    entry - at its *first* colon, which is correct for a name or an IPv4
    address and lossy for a bracketed IPv6 literal, where the first colon is
    inside the address: ``[::1]:5000`` and ``[::2]:5000`` both reduce to
    ``[``, so an entry naming the IPv6 loopback admits every compressed IPv6
    literal.  Here the literal ends at its closing bracket and only what
    follows it may be a port.

    Args:
        value: A ``Host`` header value or an allowlist entry, or anything at
            all.

    Returns:
        The host part with its brackets kept and its port removed, folded to
        lower case so that a hexadecimal literal and a hostname both compare
        case-insensitively as the HTTP grammar requires.  The empty string for
        a value that is not a host this function can judge - a non-string, an
        unterminated literal, a non-numeric port, or anything after the
        closing bracket that is not a port - which every caller treats as "not
        trusted" rather than as a match.

    """
    if not isinstance(value, str):
        return ""
    candidate = value.strip().lower()
    if not candidate.startswith(_IPV6_OPEN):
        host, separator, port = candidate.partition(_PORT_SEPARATOR)
        if separator and not port.isdigit():
            return ""
        return host
    closing = candidate.find(_IPV6_CLOSE)
    if closing == -1:
        return ""
    literal, remainder = candidate[: closing + 1], candidate[closing + 1 :]
    if not remainder:
        return literal
    if not remainder.startswith(_PORT_SEPARATOR) or not remainder[1:].isdigit():
        return ""
    return literal


def _reject_untrusted_host() -> None:
    """Refuse a bracketed IPv6 ``Host`` the allowlist does not name exactly.

    Registered as a before-request hook, so it runs **before any view
    function, template or artifact read**, and it narrows the framework's own
    check rather than replacing it.  The division is exactly the division of
    what that check gets right:

    * A name or an IPv4 address is judged by Werkzeug, whose comparison is
      exact for those and which refuses one it does not trust while the
      request context is still being built - earlier than any hook can run.
      Its documented suffix syntax (a leading dot accepting subdomains) keeps
      working untouched, which matters for a deployment that overrides the
      allowlist.
    * A bracketed IPv6 literal is judged here, because that comparison
      truncates every one of them to a single ``[`` and therefore cannot tell
      the loopback from any other address.  :func:`_host_without_port`
      explains the truncation; this hook is what closes it.

    The allowlist is read from the live configuration, so an application built
    with a ``TRUSTED_HOSTS`` override is judged against the override and never
    against :data:`_TRUSTED_HOSTS`; a configuration that names no trusted host
    at all - ``None`` or an empty collection - means the framework itself
    accepts every name, and this hook adds no restriction of its own to that
    decision.

    Matching is exact rather than semantic: ``[::1]`` is trusted because the
    allowlist names it, while ``[0:0:0:0:0:0:0:1]`` is refused although it
    denotes the same address.  An allowlist that expanded a spelling would
    stop being an allowlist, and the cost of the strictness is one documented
    spelling rather than a class of address.

    Returns:
        ``None`` when the request may proceed, which is also what a hook
        returns to let it proceed.

    Raises:
        werkzeug.exceptions.BadRequest: Through ``abort``, for a bracketed
            literal the allowlist does not name and for a bracketed value that
            cannot be parsed as a host at all.  The status is the framework's
            own for an untrusted name, so the two refusals look alike.

    """
    import logging  # noqa: PLC0415,RUF100

    from flask import abort, current_app, request  # noqa: PLC0415,RUF100

    configured = current_app.config.get(_TRUSTED_HOSTS_KEY)
    if not configured:
        return
    entries = [configured] if isinstance(configured, str) else list(configured)

    raw = request.environ.get(_HOST_ENVIRON_KEY, "")
    if not isinstance(raw, str) or not raw.strip().startswith(_IPV6_OPEN):
        return

    host = _host_without_port(raw)
    allowed = {
        candidate
        for candidate in (_host_without_port(entry) for entry in entries)
        if candidate.startswith(_IPV6_OPEN)
    }
    if host and host in allowed:
        return

    # WARNING, and the one value a rejection record needs: an operator looking
    # at a 400 has to know which name was claimed.  Interpolated with %r so a
    # control character in a crafted header cannot forge a second log line.
    logging.getLogger(__name__).warning(
        "Refusing a request claiming untrusted host %r", raw
    )
    abort(_UNTRUSTED_HOST_STATUS)


def create_app(config_overrides: Mapping[str, object] | None = None) -> Flask:
    """Build and return a fully wired read-only artifact-viewer application.

    The factory is free of import-time side effects and may be called any
    number of times in one interpreter: it holds no module-level state, so
    applications it returns cannot influence one another.  That is what lets
    the unit suite build an isolated application per test, and it is Flask's
    own documented Application Setup guidance.

    Every application it returns validates the request's ``Host`` header
    against :data:`_TRUSTED_HOSTS`, the three local names a loopback-bound
    viewer is reached by.  That is the default rather than a floor: a caller
    that serves the viewer under another name supplies its own
    ``TRUSTED_HOSTS`` through ``config_overrides``, which replaces the list
    outright.

    :param config_overrides:
        Optional mapping merged into the returned application's Flask config -
        for example ``{"TESTING": True}`` from a test, following Flask's
        upper-case key convention.  This is Flask's config only; the port's own
        six ``configuration.properties`` keys are never routed through here.
        ``None`` - the default - applies nothing and leaves Flask's defaults
        untouched, except that the local ``TRUSTED_HOSTS`` allowlist is in
        place either way: it is set before this mapping is merged, so a key of
        that name here wins and any other key joins it.
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

    # Host-header validation, and the port's whole answer to CWE-346.  Three
    # facts about it a reader cannot infer from the two statements below:
    #
    # * It is a *default*, not a floor.  It is set before the overrides below,
    #   so an application deliberately reached by another name replaces the
    #   whole list through ``create_app({"TRUSTED_HOSTS": [...]})`` - one
    #   place, and a revision of the security model rather than a tweak.  An
    #   override replaces the list; nothing merges the local names back in.
    #   The hook reads that same configuration, so it judges an overridden
    #   application by the override.
    # * Werkzeug compares only the text up to the first colon.  For a name or
    #   an IPv4 address that is exactly right - the port is ignored, so
    #   ``localhost:5000`` matches ``localhost``.  For a bracketed IPv6
    #   literal it is lossy: every one of them truncates to ``[``, so the
    #   loopback entry would otherwise admit ``[::2]`` and any other
    #   compressed literal.  :func:`_reject_untrusted_host` closes that,
    #   exactly and before any view runs, which is why the allowlist is
    #   enforced by two statements rather than one.  The bind is not the
    #   control: ``wsgi.py``'s callable can be served anywhere.
    # * A rejected ``Host`` is answered **400 before any view runs**, whichever
    #   half refused it - Werkzeug's while the request context resolves the
    #   host, the hook's before dispatch - so no route function, no template
    #   and no artifact read is reached on behalf of an untrusted name.
    #
    # A list copy, so no caller can mutate the module constant by reaching
    # through one application's config and changing the default for the next.
    flask_app.config[_TRUSTED_HOSTS_KEY] = list(_TRUSTED_HOSTS)
    flask_app.before_request(_reject_untrusted_host)

    # Applied before anything reads the config, so a value under test is in
    # place for every registration that follows.
    if config_overrides is not None:
        flask_app.config.update(config_overrides)

    # Console logging for the viewer.  Idempotent by contract - it replaces the
    # handlers it owns rather than stacking new ones - so building several
    # applications in one interpreter does not multiply output.  Configuration
    # is per process, by that process's own entry point: this factory for the
    # viewer, the ``run-tests`` command for a run, and the pool task in
    # app/services/test_run_service.py for a worker, which inherits no logging
    # state.  Every other module acquires its logger straight from the standard
    # library, which is what keeps app/utils free of any app-package import.
    configure_logging()

    flask_app.register_blueprint(web_bp)

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

    logging.getLogger(__name__).debug(
        "Read-only artifact viewer application created; %d override(s) applied",
        len(config_overrides) if config_overrides is not None else 0,
    )

    return flask_app
