"""Tests for the read-only viewer's HTTP surface.

This module is the gate for every template the viewer renders and for the two
error handlers registered by ``app/errors.py``.  It is written to be **added
to**: the assertions below cover the internal-error page,
``app/templates/errors/500.html``, whose whole contract is what it must *not*
contain, and the per-route coverage the specification requires for the six
blueprint endpoints -- each route's success case, each cause of a 404, the
artifact allowlist and the traversal rejection -- belongs in this same module
alongside them.  Nothing here forecloses that; the helpers at the top are
deliberately generic.

Two dependencies of this module are written by other parts of the port and may
not be present when it runs:

``app.create_app``
    The application factory.  When it is importable, the handler-path tests run
    against a real application, with a test-only rule added to it so that a
    handler exception can be provoked without any production route raising.
    When it is not, they run against a locally assembled Flask application that
    reproduces exactly the two things the template depends on -- the ``web``
    blueprint's six endpoint names, so ``url_for`` resolves, and a 500 handler
    that renders the template with no context -- and the assertions that are
    genuinely about the factory's own wiring skip rather than pass vacuously.

``tests/conftest.py``
    Owns ``sys.path`` for the suite, as ``pytest.ini`` records.  This module
    therefore never manipulates the import path: it probes for the factory and
    adapts, which keeps that responsibility in one place.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from pathlib import Path
from typing import Any, Callable

import pytest
from flask import Blueprint, Flask, render_template
from jinja2 import StrictUndefined

# --------------------------------------------------------------------------
# Locations and fixed names
# --------------------------------------------------------------------------

#: Repository root, from this file's own position: tests/ -> repository root.
REPO_ROOT: Path = Path(__file__).resolve().parents[1]

TEMPLATE_DIR: Path = REPO_ROOT / "app" / "templates"
STATIC_DIR: Path = REPO_ROOT / "app" / "static"

#: The template name the 500 handler resolves, and the name this suite uses.
ERROR_500_TEMPLATE: str = "errors/500.html"

#: The path of the test-only rule used to provoke a handler exception.  It is
#: added to the application under test inside the fixture and exists nowhere
#: else, so no production route has to be able to fail for these tests to run.
PROBE_RULE: str = "/blitzy-internal-error-probe"

#: The six endpoints the blueprint carries, with the rules they are bound to.
#: app/web/__init__.py calls the blueprint name "web" a contract rather than a
#: preference, and base.html builds every URL against these names; the stub
#: blueprint below reproduces them so url_for resolves when the real factory is
#: unavailable.
WEB_ENDPOINT_RULES: tuple[tuple[str, str], ...] = (
    ("index", "/"),
    ("reports_overview", "/reports"),
    ("report_feature", "/reports/features/<int:findex>"),
    ("report_scenario", "/reports/features/<int:findex>/scenarios/<int:sindex>"),
    ("reports_summary", "/reports/summary"),
    ("artifact", "/artifacts/<path:name>"),
)


class ProbeError(RuntimeError):
    """Raised by the test-only rule to drive the 500 handler.

    Its class name and its message are both distinctive so that the
    disclosure assertions can look for them in the response body and fail if
    either leaked out of the log and into the page.
    """


#: Distinctive text carried by the provoked exception.  Deliberately free of
#: characters that HTML escaping would alter, so that finding it in the body
#: cannot be defeated by escaping and missing it cannot be an artefact of it.
PROBE_MESSAGE: str = "blitzy-probe-secret-detail-must-not-be-disclosed"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _factory() -> Callable[[], Flask] | None:
    """Return ``app.create_app`` when it is importable, else ``None``.

    ``app/`` carries no ``__init__.py`` until the factory is written, and an
    ``app`` name may resolve as an implicit namespace package in the meantime,
    so the attribute is checked rather than the import alone.
    """
    if importlib.util.find_spec("app") is None:
        return None
    try:
        module = importlib.import_module("app")
    except ImportError:
        return None
    candidate = getattr(module, "create_app", None)
    return candidate if callable(candidate) else None


def _stub_blueprint() -> Blueprint:
    """A ``web`` blueprint carrying the six endpoint names and nothing else."""
    blueprint = Blueprint("web", __name__)

    def _unused(**_kwargs: Any) -> str:  # pragma: no cover - never requested
        # The stub exists so that url_for resolves; no test issues a request
        # against it, and the error page links to "/" rather than calling it.
        return ""

    for endpoint, rule in WEB_ENDPOINT_RULES:
        blueprint.add_url_rule(rule, endpoint, _unused)
    return blueprint


def _local_app() -> Flask:
    """Assemble the minimum application the error template needs.

    Template and static folders point at the real ones, so the template under
    test, the shell it extends and the stylesheet URL it inherits are the
    shipped files rather than copies.
    """
    flask_app = Flask(
        "blitzy_error_page_probe",
        template_folder=str(TEMPLATE_DIR),
        static_folder=str(STATIC_DIR),
    )
    flask_app.register_blueprint(_stub_blueprint())

    @flask_app.errorhandler(500)
    def _internal_error(_error: Exception) -> tuple[str, int]:
        # Mirrors the handler contract: the template name, status 500, and no
        # template context whatsoever.
        return render_template(ERROR_500_TEMPLATE), 500

    return flask_app


@pytest.fixture(name="using_real_factory")
def _using_real_factory() -> bool:
    """Whether the application under test came from ``create_app()``."""
    return _factory() is not None


@pytest.fixture(name="error_app")
def _error_app() -> Flask:
    """An application whose 500 handler can be provoked deterministically.

    Strict undefined is imposed on the Jinja environment for the whole
    fixture: the error page must render with an empty context, so any variable
    reference in it -- or in the shell it extends -- has to raise here rather
    than render as an empty string.
    """
    factory = _factory()
    if factory is None:
        flask_app = _local_app()
    else:
        try:
            flask_app = factory()
        except TypeError as exc:  # pragma: no cover - factory signature drift
            pytest.skip(f"create_app() is not callable with no arguments: {exc}")

    flask_app.jinja_env.undefined = StrictUndefined
    # Exceptions must reach the handler rather than the test: TESTING is left
    # off and propagation is disabled explicitly, because Flask propagates when
    # either testing or debug is on.
    flask_app.config.update(TESTING=False, DEBUG=False, PROPAGATE_EXCEPTIONS=False)

    def _raise() -> str:
        raise ProbeError(PROBE_MESSAGE)

    flask_app.add_url_rule(PROBE_RULE, "blitzy_internal_error_probe", _raise)
    return flask_app


def _error_response_body(flask_app: Flask) -> str:
    """Request the probe rule and return the rendered error page."""
    with flask_app.test_client() as client:
        response = client.get(PROBE_RULE)
    assert response.status_code == 500
    return response.get_data(as_text=True)


def _main_region(html: str) -> str:
    """The markup inside ``<main>`` - the region an extending template owns.

    Several structural assertions have to be scoped this way rather than run
    over the whole document, and stating why keeps them from being vacuous:
    base.html itself emits the deferred ``report.js`` tag, the stylesheet link
    and one behaviour hook on the ``main`` element.  Those belong to the shell.
    What this file contributes is exactly what lies between the tags below.
    """
    match = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.DOTALL | re.IGNORECASE)
    assert match is not None, "the shell did not render a main region"
    return match.group(1)


def _visible_text(html: str) -> str:
    """Text a reader sees: markup removed, entities left as written."""
    without_head = re.sub(r"<head\b.*?</head>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    without_script = re.sub(
        r"<script\b.*?</script>", " ", without_head, flags=re.DOTALL | re.IGNORECASE
    )
    return re.sub(r"<[^>]+>", " ", without_script)


def _attribute_values(html: str, *, excluding: frozenset[str]) -> list[str]:
    """Every quoted attribute value in ``html`` except the named attributes."""
    return [
        value
        for name, value in re.findall(r'([a-zA-Z-]+)="([^"]*)"', html)
        if name.lower() not in excluding
    ]


# --------------------------------------------------------------------------
# errors/500.html - renders in isolation
# --------------------------------------------------------------------------


def test_error_500_renders_with_empty_context_under_strict_undefined(
    error_app: Flask,
) -> None:
    """The page must render with no context at all, and not by luck.

    The handler passes none, so a single variable reference would turn every
    500 into a template error.  Strict undefined is what makes this a proof:
    without it an unknown name renders as an empty string and the omission
    goes unnoticed.
    """
    with error_app.test_request_context("/"):
        html = render_template(ERROR_500_TEMPLATE)

    assert html.strip(), "the error page rendered empty"
    assert "Internal server error" in html


def test_error_500_has_exactly_one_h1_and_a_populated_title(error_app: Flask) -> None:
    """One h1, rendered by the shell from this page's title block."""
    with error_app.test_request_context("/"):
        html = render_template(ERROR_500_TEMPLATE)

    assert len(re.findall(r"<h1\b", html, re.IGNORECASE)) == 1
    title = re.search(r"<title\b[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    assert title is not None
    assert title.group(1).strip(), "the document title is empty"
    # The title block feeds both the document title and the single heading.
    assert "Internal server error" in title.group(1)
    heading = re.search(r"<h1\b[^>]*>(.*?)</h1>", html, re.DOTALL | re.IGNORECASE)
    assert heading is not None
    assert "Internal server error" in heading.group(1)


# --------------------------------------------------------------------------
# errors/500.html - the handler path
# --------------------------------------------------------------------------


def test_error_500_handler_answers_with_the_page_and_status_500(
    error_app: Flask,
) -> None:
    body = _error_response_body(error_app)

    assert "Internal server error" in body
    assert "tqa-empty" in body, "the message box class is missing"


def test_error_500_response_carries_no_exception_detail(error_app: Flask) -> None:
    """No traceback, no frame, no exception class and no exception message.

    The traceback markers are matched as patterns rather than as bare words on
    purpose.  A plain search for the word "line" would match the shell's own
    footer sentence about starting runs from the command line, which would make
    the assertion fail for a reason that has nothing to do with disclosure; the
    frame pattern below is what a traceback actually looks like.
    """
    body = _error_response_body(error_app)

    assert "Traceback" not in body
    assert "Exception" not in body
    assert not re.search(r'File "[^"]*", line \d+', body)
    assert not re.search(r"\bline \d+", body)
    assert ProbeError.__name__ not in body
    assert PROBE_MESSAGE not in body
    assert "RuntimeError" not in body


def test_error_500_traceback_is_logged_and_absent_from_the_response(
    error_app: Flask,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both halves of the contract, asserted together.

    The diagnostic has to exist, and it has to exist in the log rather than in
    the page.  The log is read through the runner's own capture rather than
    through the error stream, for a reason worth recording: the runner installs
    a handler on the root logger, and Flask adds its stream handler only when
    no handler is reachable from the application logger, so under the runner it
    adds none and an error-stream assertion here would be testing the harness.
    Which stream the record lands on is asserted separately, against the real
    application, in the test below this one.
    """
    with caplog.at_level("ERROR"):
        body = _error_response_body(error_app)

    assert "Traceback" in caplog.text
    assert PROBE_MESSAGE in caplog.text
    assert PROBE_MESSAGE not in body
    assert "Traceback" not in body


def test_error_500_traceback_reaches_the_error_stream(
    error_app: Flask,
    using_real_factory: bool,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The traceback lands on the error stream, and only there.

    This is the half of the contract that belongs to ``app/errors.py`` and the
    handler split installed by ``app/logging_config.py``, which routes WARNING
    and above to the error stream.  It is therefore asserted only against an
    application built by the factory; with the local stand-in there is no such
    configuration to verify and the test skips rather than passing vacuously.
    Capture is at file-descriptor level so that a handler holding a reference
    to the original stream is captured as well as one resolved per request.
    """
    if not using_real_factory:
        pytest.skip("app.create_app() is not importable yet")

    body = _error_response_body(error_app)
    captured = capfd.readouterr()

    assert "Traceback" in captured.err
    assert PROBE_MESSAGE in captured.err
    assert PROBE_MESSAGE not in captured.out
    assert PROBE_MESSAGE not in body
    assert "Traceback" not in body


def test_error_500_handler_is_registered_by_the_factory(
    error_app: Flask,
    using_real_factory: bool,
) -> None:
    """The factory itself wires the handler - skipped until it exists."""
    if not using_real_factory:
        pytest.skip("app.create_app() is not importable yet")

    body = _error_response_body(error_app)
    assert "Internal server error" in body


# --------------------------------------------------------------------------
# errors/500.html - information disclosure
# --------------------------------------------------------------------------


def test_error_500_discloses_no_configuration_or_filesystem_detail(
    error_app: Flask,
) -> None:
    """Nothing about configuration, paths, versions or the host.

    Scope, stated so the assertion cannot be vacuous: the checks run over the
    page's visible text and over the region this template owns with the ``href``
    attribute excluded.  One href is required -- the link ``url_for`` builds to
    the index route -- and it is allowlisted by exactly its own value rather
    than by ignoring hrefs in general.  The shell's stylesheet and script URLs
    live in the head and after the body's content and belong to the shell.
    """
    body = _error_response_body(error_app)
    region = _main_region(body)
    text = _visible_text(body)

    with error_app.test_request_context("/"):
        from flask import url_for

        allowed_href = url_for("web.index")

    hrefs = re.findall(r'href="([^"]*)"', region)
    assert hrefs == [allowed_href], f"unexpected link targets in the page: {hrefs}"

    # The six configuration keys, by name, and the artifact vocabulary.
    for forbidden in (
        "browser",
        "web.table.url",
        "url",
        "username",
        "password",
        "EmplTitle",
        "configuration.properties",
        "target",
        "json",
    ):
        assert forbidden not in text, f"visible text discloses {forbidden!r}"

    # Path-like strings, version strings, and host:port pairs.  Checked over
    # the owned region with href excluded, so the one required link cannot
    # satisfy the check by accident and cannot break it either.
    region_without_hrefs = re.sub(r'href="[^"]*"', "", region)
    assert not re.search(r"[A-Za-z]:\\", region_without_hrefs)
    assert not re.search(r"(?<![a-zA-Z])/(?:home|root|tmp|usr|var|opt)/", region_without_hrefs)
    assert not re.search(r"\b\d+\.\d+(?:\.\d+)?\b", region_without_hrefs)
    assert not re.search(r"\blocalhost\b|\b127\.0\.0\.1\b|:\d{2,5}\b", region_without_hrefs)
    assert not re.search(r"\bPython\b|\bFlask\b|\bWerkzeug\b|\bJinja\b", region_without_hrefs)


def test_error_500_emits_no_link_opening_attribute(error_app: Flask) -> None:
    """No attribute that opens a link elsewhere, anywhere in the document."""
    body = _error_response_body(error_app)

    assert not re.search(r"\btarget\s*=", body, re.IGNORECASE)


def test_error_500_echoes_nothing_the_caller_supplied(error_app: Flask) -> None:
    """Nothing the request carried comes back in the page.

    The page is static, so this cannot fail by construction -- which is the
    point of asserting it: a later edit that reached for the request object to
    be helpful would fail here.  The probe value is pushed through the query
    string, a header and the user agent, and the page is compared byte for byte
    with the one served for a bare request.
    """
    echo = "blitzy-request-echo-probe-value"

    with error_app.test_client() as client:
        plain = client.get(PROBE_RULE)
        decorated = client.get(
            f"{PROBE_RULE}?leak={echo}",
            headers={"X-Blitzy-Probe": echo, "Referer": f"https://{echo}.invalid/"},
            environ_overrides={"HTTP_USER_AGENT": echo},
        )

    assert plain.status_code == 500
    assert decorated.status_code == 500
    plain_body = plain.get_data(as_text=True)
    decorated_body = decorated.get_data(as_text=True)
    assert echo not in decorated_body
    assert decorated_body == plain_body


# --------------------------------------------------------------------------
# errors/500.html - structure
# --------------------------------------------------------------------------


def test_error_500_emits_no_inline_behaviour_styling_or_submission(
    error_app: Flask,
) -> None:
    """No inline script, no style element, no form, and no external URL.

    Scope, again stated: the shell emits one deferred script element with a
    ``src``, so the page-wide assertion is that no script element carries
    inline code and that the region this template owns carries no script element
    at all.  Style elements, forms and absolute URLs are absent document-wide.
    """
    body = _error_response_body(error_app)
    region = _main_region(body)

    inline_scripts = [
        block
        for block in re.findall(r"<script\b([^>]*)>(.*?)</script>", body, re.DOTALL | re.IGNORECASE)
        if block[1].strip() or "src=" not in block[0].lower()
    ]
    assert inline_scripts == []
    assert not re.search(r"<script\b", region, re.IGNORECASE)
    assert not re.search(r"<style\b", body, re.IGNORECASE)
    assert not re.search(r"<form\b", body, re.IGNORECASE)
    assert not re.search(r"<button\b", body, re.IGNORECASE)
    assert "http://" not in body
    assert "https://" not in body
    assert 'style="' not in body
    # No protocol-relative reference either.
    assert not re.search(r'(?:href|src)="//', body)


def test_error_500_emits_no_state_or_behaviour_hook_of_its_own(
    error_app: Flask,
) -> None:
    """State comes from absence; the shell owns the one behaviour hook.

    The three boolean state attributes mean expanded, visible and closed when
    absent, and the behaviour script sets its own hooks at run time, so server
    output carries none of them.  ``data-report-root`` on the ``main`` element is
    the shell's, which is why this is scoped to the owned region.
    """
    body = _error_response_body(error_app)
    region = _main_region(body)

    for attribute in ("data-tqa-collapsed", "data-tqa-filtered", "data-tqa-open"):
        assert attribute not in body

    assert not re.search(r"\bdata-report-[a-z-]+", region)
    assert not re.search(r"\bdata-tqa-status\b", region)
    # The report stylesheet's monospace failure-text treatment must not dress
    # this page, which is forbidden from carrying failure text at all.
    assert "tqa-error" not in body


def test_error_500_marks_no_navigation_item_as_current(error_app: Flask) -> None:
    """The proof that the active-navigation variable was left unset."""
    body = _error_response_body(error_app)

    assert 'aria-current="page"' not in body
    assert "aria-current" not in body


def test_error_500_uses_only_declared_stylesheet_class_names(
    error_app: Flask,
) -> None:
    """Every class in the owned region is one the stylesheet declares."""
    region = _main_region(_error_response_body(error_app))
    stylesheet = (STATIC_DIR / "css" / "main.css").read_text(encoding="utf-8")
    declared = set(re.findall(r"\.(tqa-[a-z0-9-]+)", stylesheet))

    used: set[str] = set()
    for value in _attribute_values(region, excluding=frozenset({"href"})):
        used.update(token for token in value.split() if token.startswith("tqa-"))

    assert used, "the region carries no namespaced class at all"
    assert used <= declared, f"undeclared class names: {sorted(used - declared)}"
    assert "tqa-empty" in used
    assert "tqa-link" in used


def test_error_500_body_is_identical_across_repeated_requests(
    error_app: Flask,
) -> None:
    """A fully static page cannot vary between two identical requests."""
    first = _error_response_body(error_app)
    second = _error_response_body(error_app)

    assert first == second
