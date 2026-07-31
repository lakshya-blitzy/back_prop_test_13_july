"""Integration tests for the rendered report surface and the rendered error pages.

WHAT THIS MODULE PROVES
-----------------------
This is the web half of validation criterion V11: "an unknown route returns 404
from the application-level error handler (registered on the app, not on a
blueprint, so it actually fires)" and the two rendered pages behave as specified.
Concretely it asserts, executably:

* ``GET /`` and ``GET /reports`` answer 200 with an HTML, UTF-8 response and both
  extend the shared layout ``app/templates/base.html``;
* ``GET /reports`` lists all four report artifacts declared by the documented test
  runner plus the screen shots and the error shots;
* every rendered artifact path is spelled beneath the literal artifact root
  ``target``;
* both pages render cleanly when nothing at all has been generated;
* the generated Cucumber HTML report is *referenced*, never re-rendered or
  embedded;
* an unknown URL is answered by the project's own rendered 404 page rather than
  the framework's default one;
* a wrong-method request to a real rule is answered by the application-level 405
  handler, without any fifth error template existing;
* an unhandled exception is answered by the project's own rendered 500 page;
* the factory hands every test its own application, so nothing leaks between
  tests or between parallel workers.

The ``/api/v1`` half of the same criterion -- where the identical handlers answer
with a structured JSON envelope instead of a page, which is the dual shape the
enterprise baseline asks for -- belongs to ``tests/integration/test_api_routes.py``
and is deliberately not duplicated here.

SOURCE LINEAGE
--------------
The surface under test is the port of the report sections of the project README,
``[README.md:L152-L161]`` -- ``### Jenkins Cucumber Reports`` (L152),
``##### HTML Report:`` (L155) and ``##### Txt Report:`` (L159) -- together with
the reporting prose at ``[README.md:L42-L43]``, which records that the project
generates JSON, HTML and Txt reports, "screen shots" for tests "if you enable
it" and "error shots" for failed test cases.

The four artifact paths asserted below are quoted verbatim from the plugin block
of the documented runner: ``[README.md:L79]`` ``"html:target/cucumber-reports.html",``,
``[README.md:L80]`` ``"json:target/cucumber.json",``, ``[README.md:L81]``
``"rerun:target/rerun.txt",`` and ``[README.md:L82]``
``"me.jvt.cucumber.report.PrettyReports:target/cucumber"``. The literal strings
are what is matched; the line numbers are documentation only.

The artifact root keeps its Java-flavoured name because the CI publisher selects
reports with ``fileIncludePattern: '**/*.json'`` ``[Jenkins:L15]`` and that value
needs no edit at all while the output stays underneath ``target/``. Renaming the
root to ``build``, ``out``, ``dist``, ``reports`` or ``artifacts`` would silently
break that contract, so :class:`TestRenderedReportSurface` asserts the literal
spelling rather than trusting it.

THERE IS NO PRODUCT USER INTERFACE HERE
---------------------------------------
The source project is a Java/Maven Selenium-Cucumber automation scaffold of
exactly five tracked files that requires no user interface at all: no HTML,
stylesheet, script or image asset has ever existed anywhere in this repository's
history. The Flask port therefore introduces a delivery mechanism for artifacts
that already existed, and nothing more. The only real interface in the picture is
the *external* application under test -- its French-language login page and
dashboard -- which Selenium drives and which is out of scope.

Two consequences bind this module. First, the Design System Alignment Protocol is
not triggered: no component library, design system or proprietary UI library is
named anywhere, and no design frames were supplied. So nothing below asserts a
design token, a component-library class name, a colour, a spacing value, a font
or a layout measurement, and there is no visual-regression, screenshot-diff or
pixel comparison of any kind. Second, the *absence* of a framework is itself a
requirement, so :class:`TestNoFrameworkLeakedIn` asserts it positively: no
external or content-delivery reference, no client-side code, exactly one
stylesheet, and no framework class signature.

RULES STATUS
------------
No user-specified rules were provided for this project: the rules document is
empty on a full read. Nothing here is written to satisfy a rule and no rule is
invented. The absence of rules is not permission to lower the bar, so the work is
held to the plan's enterprise baseline instead -- full type annotations, the
application obtained through the factory rather than a module global, explicit
UTF-8 on every byte stream, no subprocess and no shell invocation, no console
writes, markers and warning filters left entirely to ``pytest.ini``, and every
claim asserted rather than commented.

HOW TO RUN IT
-------------
``make test-integration``, which overrides the preserved default tag selector with
``-m ""``. Running ``pytest`` bare applies ``addopts = -m "LogOut"`` from
``pytest.ini`` -- the faithful port of ``tags = "@LogOut"`` ``[README.md:L87]`` --
which selects no scenario at all (defect D2) and therefore deselects every test in
this module too. That is preserved behaviour, not a misconfiguration.

WHY THERE IS NO LIVE SERVER HERE
--------------------------------
Every request below is issued through Flask's in-process test client. No server is
started, no port is bound and ``requests`` is not imported: the suite runs in
parallel by default, so a bound port would be a collision waiting to happen, and
the sandbox has neither a running service nor network access. The client
dispatches through the very application the factory built, which is what makes
these assertions assertions about the real wiring.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, NoReturn

import pytest

# ---------------------------------------------------------------------------
# THIRD-PARTY GUARD -- must precede every ``app`` import below.
#
# Importing any ``app.*`` module executes ``app/__init__.py`` first, and that
# module IS the Flask application factory, so every import in the block that
# follows transitively requires Flask; the templates additionally require Jinja2.
# Skipping at module level turns a missing optional distribution into a reported
# skip instead of a collection error, which keeps the suites that need no
# third-party package at all -- the parity suite above everything -- runnable in
# an environment that cannot build the application.
#
# ``exc_type=ImportError`` is passed deliberately and is not the default. Since
# pytest 9.1 the guard skips only on ``ModuleNotFoundError``, so a distribution
# that is present but unusable -- a half-installed wheel, or one whose own
# dependency is missing -- would raise straight through and fail collection for
# this module. Widening it to ``ImportError`` closes that gap, and it narrows
# nothing that matters: it applies to importing the third-party package alone, so a
# genuine fault inside the ``app`` package below still fails loudly, exactly as it
# should.
#
# The imports below are therefore deliberately not at the top of the file, and
# ``E402`` is silenced on each of them for exactly that reason.
# ---------------------------------------------------------------------------
pytest.importorskip(
    "flask",
    reason="Flask is required to build the application under test through its factory",
    exc_type=ImportError,
)
pytest.importorskip(
    "jinja2",
    reason="Jinja2 is required to render the report surface and the error pages",
    exc_type=ImportError,
)

from app import create_app  # noqa: E402
from app.errors import (  # noqa: E402
    HTTP_INTERNAL_SERVER_ERROR,
    HTTP_METHOD_NOT_ALLOWED,
    HTTP_NOT_FOUND,
    INTERNAL_SERVER_ERROR_TEMPLATE,
    NOT_FOUND_TEMPLATE,
)
from app.reporting.screenshots import ShotCategory, shot_directory  # noqa: E402
from app.utils import paths  # noqa: E402
from app.web import routes as web_routes  # noqa: E402

if TYPE_CHECKING:
    # Annotation-only imports. ``from __future__ import annotations`` keeps every
    # annotation a string, so this block never executes at run time and costs
    # nothing, while ``mypy`` still sees the real types.
    from pathlib import Path

    from flask import Flask
    from flask.testing import FlaskClient
    from werkzeug.wrappers import Response


# =============================================================================
# Expected media types.
#
# Naming convention for the rest of this module: a name WITHOUT a leading
# underscore states an expectation this module asserts against -- a media type, a
# template marker, a verbatim source literal -- so that every expected value is
# greppable and documented in one place. A name WITH a leading underscore is
# machinery: a compiled matcher or a helper. Nothing here is imported by another
# module, and nothing should be.
# =============================================================================

HTML_MIMETYPE: Final[str] = "text/html"
"""The media type both rendered pages and both error pages must answer with."""

CSS_MIMETYPE: Final[str] = "text/css"
"""The media type the single hand-written stylesheet must be served with."""

UTF8_CHARSET_PARAMETER: Final[str] = "charset=utf-8"
"""The charset parameter every HTML response must carry, lower-cased for matching."""

STYLESHEET_URL_PATH: Final[str] = "/static/css/main.css"
"""The one stylesheet in the whole surface, served by the application itself."""


# =============================================================================
# Template markers.
#
# Each tuple holds strings that ONLY the named template can produce. That is the
# point of them: a status code alone proves nothing, because the framework's own
# default error pages carry the same statuses. Matching a marker is what proves
# the project's own template rendered, and matching a base-layout marker is what
# proves ``base.html`` is a real shared layout rather than a decorative file.
# =============================================================================

BASE_LAYOUT_MARKERS: Final[tuple[str, ...]] = (
    "<!DOCTYPE html>",
    '<html lang="en">',
    '<meta charset="utf-8">',
    f'<link rel="stylesheet" href="{STYLESHEET_URL_PATH}">',
    "<p>Testinium-QA</p>",
    '<a href="/">Home</a>',
    '<a href="/reports">Reports</a>',
    "Serves the generated test reports, screen shots and error shots over HTTP.",
)
"""Markers contributed by ``app/templates/base.html`` alone."""

INDEX_MARKERS: Final[tuple[str, ...]] = (
    "<title>Home - Testinium-QA</title>",
    "<h1>Testinium-QA</h1>",
)
"""Markers contributed by ``app/templates/index.html`` alone."""

REPORTS_MARKERS: Final[tuple[str, ...]] = (
    "<title>Report artifacts - Testinium-QA</title>",
    "<h1>Report artifacts</h1>",
)
"""Markers contributed by ``app/templates/reports.html`` alone."""

NOT_FOUND_MARKERS: Final[tuple[str, ...]] = (
    "<title>Page or method not available - Testinium-QA</title>",
    "<h1>Page or method not available</h1>",
)
"""Markers contributed by ``app/templates/errors/404.html`` alone."""

INTERNAL_SERVER_ERROR_MARKERS: Final[tuple[str, ...]] = (
    "<title>Internal server error - Testinium-QA</title>",
    "<h1>Something went wrong</h1>",
)
"""Markers contributed by ``app/templates/errors/500.html`` alone."""

FRAMEWORK_DEFAULT_ERROR_MARKERS: Final[tuple[str, ...]] = (
    "<title>404 Not Found</title>",
    "<title>405 Method Not Allowed</title>",
    "<title>500 Internal Server Error</title>",
    "<h1>Not Found</h1>",
    "<h1>Method Not Allowed</h1>",
    "<h1>Internal Server Error</h1>",
)
"""Markers the framework's own default error pages carry.

Every one of them must be ABSENT from an error response. This is the assertion
that distinguishes "the application-level handler fired" from "the framework
answered for us", and it is the only way to tell the two apart: both return the
same status code, which is exactly why a status-only assertion would pass
falsely.
"""

EMPTY_STATE_MARKER: Final[str] = "<em>Not generated yet</em>"
"""How the report index renders an artifact that has not been generated."""

AVAILABLE_STATE_MARKER: Final[str] = "<strong>Available</strong>"
"""How the report index renders an artifact it can serve right now."""


# =============================================================================
# Source literals, quoted verbatim.
#
# The four plugin declarations of the documented test runner. Matching the
# literal string is deliberate: the plan's body cites these as lines 78 to 81
# while the verified truth is 79 to 82, so the strings are authoritative and the
# line numbers are documentation.
# =============================================================================

PLUGIN_DECLARATIONS: Final[tuple[str, ...]] = (
    "html:target/cucumber-reports.html",
    "json:target/cucumber.json",
    "rerun:target/rerun.txt",
    "me.jvt.cucumber.report.PrettyReports:target/cucumber",
)
"""The four report plugins declared at ``[README.md:L79-L82]``, verbatim."""

FORBIDDEN_ARTIFACT_ROOTS: Final[tuple[str, ...]] = (
    "build/",
    "out/",
    "dist/",
    "reports/",
    "artifacts/",
    "output/",
)
"""Artifact-root spellings that must never appear in a rendered page.

The publisher glob ``fileIncludePattern: '**/*.json'`` ``[Jenkins:L15]`` keeps
working only while the output stays underneath the original root, so a
"modernised" root name is a defect and not a refinement. Configuration values are
data: no value is rounded, renamed or modernised.
"""

FRAMEWORK_CLASS_SIGNATURES: Final[tuple[str, ...]] = (
    "bootstrap",
    "tailwind",
    "bulma",
    "foundation",
    "materialize",
    "mui-",
    "ant-",
    "chakra",
    "semantic-ui",
    "font-awesome",
    "jquery",
    "react",
    "vue",
    "angular",
    "svelte",
    "htmx",
    "alpine",
)
"""Substrings that would betray a CSS framework, component library or client-side
framework having crept into the rendered markup. None may appear: the surface is
plain semantic HTML with one hand-written stylesheet, and no component library,
CSS framework, design token system or client-side framework is introduced.

Matched case-insensitively against the whole rendered document rather than against
class attributes alone, so a signature hiding in a URL is caught as well. Jinja
comments never reach the output, so only real markup and real prose are searched;
a prose collision would therefore be a deliberate signal to review the wording,
never a silent pass.
"""


# =============================================================================
# Matchers.
# =============================================================================

_STYLESHEET_LINK_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"""<link\b[^>]*\brel\s*=\s*["']stylesheet["'][^>]*>""",
    re.IGNORECASE,
)
"""Any ``<link rel="stylesheet">`` element, however its attributes are ordered."""

_LINK_HREF_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"""\bhref\s*=\s*["']([^"']*)["']""",
    re.IGNORECASE,
)
"""The ``href`` value of a single element."""

_EXTERNAL_REFERENCE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"""\b(?:src|href)\s*=\s*["']\s*(?:[a-zA-Z][a-zA-Z0-9+.\-]*:)?//""",
    re.IGNORECASE,
)
"""An absolute or protocol-relative ``src``/``href`` -- i.e. an external request.

Matched on the attribute rather than on the bare text ``http``, because both
pages legitimately use the word "HTTP" in prose and a naive substring search
would report that as a content-delivery link.
"""

_CLASS_ATTRIBUTE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"""\bclass\s*=\s*["']""",
    re.IGNORECASE,
)
"""A ``class`` attribute. The stylesheet styles semantic elements rather than a
class taxonomy, so the rendered markup carries none at all, and a class attribute
appearing here would be the first sign of component-library markup.
"""


# =============================================================================
# Helpers.
#
# Every one of them is a pure read over a response or over the file system. None
# starts a process, opens a socket or writes anything, and every text read passes
# an explicit UTF-8 encoding.
# =============================================================================


def _body_of(response: Response) -> str:
    """Return *response*'s payload decoded explicitly as UTF-8.

    The decoding is spelled out rather than left to the framework's default so
    that the assertions below are made against exactly the bytes the client
    received, on every platform and under every locale.

    Args:
        response: The response to read.

    Returns:
        The decoded body.
    """
    return response.get_data(as_text=False).decode("utf-8")


def _content_type_of(response: Response) -> str:
    """Return *response*'s ``Content-Type`` header, lower-cased for matching.

    Args:
        response: The response to inspect.

    Returns:
        The header value, or an empty string when the response carries none.
    """
    return response.headers.get("Content-Type", "").lower()


def _assert_html_utf8(response: Response) -> None:
    """Assert *response* is an HTML document declared as UTF-8.

    Args:
        response: The response to check.
    """
    content_type = _content_type_of(response)
    assert HTML_MIMETYPE in content_type, f"expected an HTML response, got {content_type!r}"
    assert (
        UTF8_CHARSET_PARAMETER in content_type
    ), f"expected a UTF-8 charset parameter, got {content_type!r}"


def _assert_markers_present(markup: str, markers: tuple[str, ...], description: str) -> None:
    """Assert every marker in *markers* appears in *markup*.

    Args:
        markup: The rendered document.
        markers: The strings that must all be present.
        description: What the markers identify, used in the failure message.
    """
    missing = [marker for marker in markers if marker not in markup]
    assert not missing, f"{description} did not render; missing marker(s): {missing!r}"


def _assert_markers_absent(markup: str, markers: tuple[str, ...], description: str) -> None:
    """Assert no marker in *markers* appears in *markup*.

    Args:
        markup: The rendered document.
        markers: The strings that must all be absent.
        description: What the markers identify, used in the failure message.
    """
    present = [marker for marker in markers if marker in markup]
    assert not present, f"{description} unexpectedly present: {present!r}"


def _stylesheet_hrefs(markup: str) -> list[str]:
    """Return the ``href`` of every stylesheet link in *markup*, in document order.

    Args:
        markup: The rendered document.

    Returns:
        One entry per ``<link rel="stylesheet">`` element. A link without an
        ``href`` yields an empty string rather than being dropped, so a broken
        element can never hide from the count.
    """
    hrefs: list[str] = []
    for element in _STYLESHEET_LINK_PATTERN.findall(markup):
        match = _LINK_HREF_PATTERN.search(element)
        hrefs.append(match.group(1) if match else "")
    return hrefs


def _artifact_relative_paths() -> tuple[str, ...]:
    """Return the seven artifact paths the report index lists, POSIX-spelled.

    Nothing is spelled out here. Every path comes from :mod:`app.utils.paths`,
    the single owner of the artifact layout, and the two shot directories are
    resolved through :mod:`app.reporting.screenshots` -- the module that indexes
    them for the page -- rather than re-derived. The order matches the page's own
    render order: the four declared report plugins, then the screen shots, then
    the error shots, then the report directory whose name is retained for
    consumer parity.

    Returns:
        The paths relative to the repository root, spelled with forward slashes.
    """
    return (
        paths.to_posix(paths.CUCUMBER_HTML_PATH),
        paths.to_posix(paths.CUCUMBER_JSON_PATH),
        paths.to_posix(paths.RERUN_TXT_PATH),
        paths.to_posix(paths.PRETTY_REPORTS_DIR),
        paths.to_posix(shot_directory(ShotCategory.SCREEN_SHOTS)),
        paths.to_posix(shot_directory(ShotCategory.ERROR_SHOTS)),
        paths.to_posix(paths.SUREFIRE_REPORTS_DIR),
    )


def _artifact_display_names() -> tuple[str, ...]:
    """Return the seven artifact display names the report index renders.

    Taken from :mod:`app.web.routes`, which owns the page's presentation
    vocabulary, so this cannot drift away from what the view actually passes.

    Returns:
        The display names, in the page's render order.
    """
    return (
        web_routes.NAME_CUCUMBER_HTML,
        web_routes.NAME_CUCUMBER_JSON,
        web_routes.NAME_RERUN_MANIFEST,
        web_routes.NAME_PRETTY_REPORTS,
        web_routes.NAME_SCREEN_SHOTS,
        web_routes.NAME_ERROR_SHOTS,
        web_routes.NAME_SUREFIRE_REPORTS,
    )


def _always_raises() -> NoReturn:
    """Raise, so that the application-level 500 handler has something to handle.

    Registered as a view function on a dedicated application instance and never on
    a shared one -- :class:`TestErrorPages` explains why that isolation matters.

    Raises:
        RuntimeError: Always. The message is deliberately recognisable so that a
            leak of it into a rendered page would be obvious; the 500 page states
            a fixed generic sentence and never echoes an exception.
    """
    raise RuntimeError("deliberate failure raised by the 500-handler integration test")


# =============================================================================
# Requirement 1 -- the two rendered pages (criterion V11).
# =============================================================================


class TestRenderedReportSurface:
    """The two rules of the root-mounted ``web_bp`` blueprint.

    ``GET /`` and ``GET /reports`` are the last row of the closed ten-rule HTTP
    surface, and they are the whole of this blueprint: the surface was derived
    mechanically from the three pipeline stages plus the four report artifacts, so
    every other rule belongs to ``/api/v1`` or is the one additive liveness probe
    the factory registers directly on the application.

    Nothing here asserts on styling. The tests read status codes, media types,
    template selection and the artifact references the page exists to deliver --
    the page is plumbing, and it is tested as plumbing.
    """

    def test_root_route_renders_the_landing_page(self, client: FlaskClient) -> None:
        """``GET /`` answers 200 with the rendered landing page."""
        response = client.get("/")

        assert response.status_code == 200
        _assert_html_utf8(response)
        _assert_markers_present(_body_of(response), INDEX_MARKERS, "the landing page")

    def test_reports_route_renders_the_report_index(self, client: FlaskClient) -> None:
        """``GET /reports`` answers 200 with the rendered report index."""
        response = client.get("/reports")

        assert response.status_code == 200
        _assert_html_utf8(response)
        _assert_markers_present(_body_of(response), REPORTS_MARKERS, "the report index")

    @pytest.mark.parametrize("path", ["/", "/reports"])
    def test_both_pages_extend_the_shared_layout(self, client: FlaskClient, path: str) -> None:
        """Both pages carry every marker only ``base.html`` can contribute.

        This is what proves the shared layout is real rather than decorative: the
        document shell, the single stylesheet reference, the two navigation links
        and the footer sentence are declared once, in ``base.html``, and appear on
        every page because each page extends it.
        """
        response = client.get(path)

        assert response.status_code == 200
        _assert_markers_present(_body_of(response), BASE_LAYOUT_MARKERS, f"the layout of {path}")

    def test_reports_page_lists_every_generated_artifact(self, client: FlaskClient) -> None:
        """The report index lists all four report artifacts and both shot directories.

        The four report paths are the four plugin outputs the documented runner
        declares at ``[README.md:L79-L82]``; the two shot directories are the
        screen shots and error shots of ``[README.md:L42-L43]``; and the retained
        Surefire report directory is listed alongside them so that a consumer keyed
        on that path still finds it. Every expected path is resolved through
        :mod:`app.utils.paths` and :mod:`app.reporting.screenshots` rather than
        written out, so this test cannot drift away from the layout it checks.
        """
        markup = _body_of(client.get("/reports"))

        for relative_path in _artifact_relative_paths():
            assert relative_path in markup, f"the report index does not list {relative_path!r}"

        for display_name in _artifact_display_names():
            assert display_name in markup, f"the report index does not name {display_name!r}"

        for declaration in PLUGIN_DECLARATIONS:
            assert declaration in markup, (
                f"the report index does not quote the source declaration {declaration!r} "
                "from the documented runner block [README.md:L79-L82]"
            )

    def test_reports_page_spells_the_artifact_root_literally(self, client: FlaskClient) -> None:
        """Every rendered artifact path sits beneath the literal root ``target``.

        The root name is a contract, not a leftover: the CI publisher selects
        reports with ``fileIncludePattern: '**/*.json'`` ``[Jenkins:L15]`` and that
        value needs no edit at all while the output stays underneath ``target/``.
        A renamed root would break the publisher silently, so both halves are
        asserted -- that the layout still spells the root ``target``, and that no
        alternative spelling appears anywhere in the rendered page.
        """
        assert paths.TARGET_DIR_NAME == "target"

        markup = _body_of(client.get("/reports"))
        prefix = f"{paths.TARGET_DIR_NAME}/"
        for relative_path in _artifact_relative_paths():
            assert relative_path.startswith(
                prefix
            ), f"{relative_path!r} is not beneath the literal artifact root {prefix!r}"

        lowered = markup.lower()
        renamed = [root for root in FORBIDDEN_ARTIFACT_ROOTS if root in lowered]
        assert not renamed, f"the report index spells the artifact root as {renamed!r}"

    @pytest.mark.parametrize("path", ["/", "/reports"])
    def test_pages_render_when_nothing_has_been_generated(
        self,
        client: FlaskClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        path: str,
    ) -> None:
        """An empty artifact tree renders 200, never 500.

        This is the ordinary state of the system, not an edge case. The artifact
        tree is git-ignored so a fresh checkout has none of it, the clean step
        wipes it before every run, and the preserved default selector
        ``tags = "@LogOut"`` ``[README.md:L87]`` matches no scenario at all
        (defect D2), so the documented run produces nothing to list. A page that
        assumed an artifact exists would fail here, which is precisely what this
        test is for.

        The artifact view is redirected by moving the working directory to a
        throwaway one: the default layout is deliberately relative, so every probe
        resolves against the process working directory and the repository's own
        ``target/`` tree is neither read nor touched.
        """
        monkeypatch.chdir(tmp_path)
        assert not (tmp_path / paths.TARGET_DIR_NAME).exists()

        response = client.get(path)

        assert response.status_code == 200
        _assert_html_utf8(response)
        _assert_markers_present(_body_of(response), BASE_LAYOUT_MARKERS, f"the layout of {path}")

    def test_empty_report_index_describes_every_missing_artifact(
        self,
        client: FlaskClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """With nothing generated, all seven rows read as not generated yet.

        An artifact that is missing is described rather than hidden, so the table
        keeps its full shape and an operator can see where each artifact will be
        written. Nothing reads as a fault, because neither an absent artifact nor a
        zero-scenario run is one here.
        """
        monkeypatch.chdir(tmp_path)

        markup = _body_of(client.get("/reports"))

        expected_rows = len(_artifact_relative_paths())
        assert markup.count(EMPTY_STATE_MARKER) == expected_rows
        assert AVAILABLE_STATE_MARKER not in markup
        for relative_path in _artifact_relative_paths():
            assert relative_path in markup

    def test_generated_html_report_is_referenced_never_re_rendered(
        self,
        client: FlaskClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """The Cucumber HTML report is served as a static artifact, not re-rendered.

        Re-rendering it would risk diverging from the source toolchain's own output,
        so the index must reference the artifact and never reproduce, embed or
        re-template its contents. The check is empirical rather than structural: a
        recognisable sentinel is written into the report artifact, and the rendered
        page must list the artifact's path, report it as available, and contain no
        trace whatsoever of the sentinel.

        The whole tree is built inside a throwaway directory by
        :func:`app.utils.paths.ensure_target_layout`, which is the single owner of
        the five-directory layout and accepts an optional base directory for
        exactly this purpose. The repository's own ``target/`` tree is never
        written to and never removed.
        """
        sentinel = "BLITZY-SENTINEL-CUCUMBER-HTML-REPORT-BODY"
        created = paths.ensure_target_layout(tmp_path)
        assert created == paths.managed_directories(tmp_path)

        layout = paths.resolve_layout(tmp_path)
        layout.cucumber_html.write_text(
            f"<html><body>{sentinel}</body></html>",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        response = client.get("/reports")
        markup = _body_of(response)

        assert response.status_code == 200
        assert paths.to_posix(paths.CUCUMBER_HTML_PATH) in markup
        assert (
            AVAILABLE_STATE_MARKER in markup
        ), "the report index did not detect the artifact that was just written"
        assert (
            sentinel not in markup
        ), "the report index embedded the generated HTML report instead of referencing it"
        for embedding_element in ("<iframe", "<embed", "<object", "<frame"):
            assert embedding_element not in markup.lower()


# =============================================================================
# Requirement 0 -- the absence of a framework, asserted positively.
# =============================================================================


class TestNoFrameworkLeakedIn:
    """No CSS framework, component library, design system or client-side code.

    Each of those absences is a requirement rather than an omission, so each is
    asserted rather than assumed. The whole presentation surface is five templates
    and one hand-written stylesheet; there is no component library to conform to,
    no design token vocabulary to reference and no design frames to match, so
    nothing here inspects a colour, a spacing value, a font or a layout -- and no
    screenshot is captured, diffed or compared.
    """

    def test_the_single_stylesheet_is_served_by_the_application(self, client: FlaskClient) -> None:
        """The one stylesheet is served locally, so no page needs an external request.

        Read inside a ``with`` block, unlike every other request in this module.
        This is the only response here that is backed by an open file rather than a
        rendered string, and closing it is not tidiness: an unclosed file handle
        surfaces as a ``ResourceWarning`` during finalisation, and ``pytest.ini``
        promotes warnings to errors, so the leak would fail this test in a way that
        pointed nowhere near its cause.
        """
        with client.get(STYLESHEET_URL_PATH) as response:
            assert response.status_code == 200
            assert CSS_MIMETYPE in _content_type_of(response)
            assert response.get_data(as_text=False), "the stylesheet was served empty"

    @pytest.mark.parametrize("path", ["/", "/reports", "/blitzy-unknown-page"])
    def test_each_page_references_exactly_one_stylesheet(
        self, client: FlaskClient, path: str
    ) -> None:
        """Every rendered page -- including an error page -- links one stylesheet.

        Exactly one, and it is the application's own. The shared layout owns that
        reference, which is why no page restates it and why a second link would
        mean something was added outside the layout.
        """
        hrefs = _stylesheet_hrefs(_body_of(client.get(path)))

        assert hrefs == [STYLESHEET_URL_PATH], f"{path} references stylesheets {hrefs!r}"

    @pytest.mark.parametrize("path", ["/", "/reports", "/blitzy-unknown-page"])
    def test_pages_make_no_external_or_content_delivery_request(
        self, client: FlaskClient, path: str
    ) -> None:
        """No page reaches off-host for a stylesheet, script, font or image.

        The rendered pages make no external network request at all: no
        content-delivery link, no web font, no icon font, no client-side code and
        no subresource-integrity attributes, which only an external reference would
        need.
        """
        markup = _body_of(client.get(path))
        lowered = markup.lower()

        external = _EXTERNAL_REFERENCE_PATTERN.findall(markup)
        assert not external, f"{path} references external resources: {external!r}"
        assert "<script" not in lowered, f"{path} carries client-side code"
        assert "integrity=" not in lowered
        assert "crossorigin=" not in lowered
        assert "onclick=" not in lowered
        assert "<style" not in lowered, f"{path} carries an inline style element"

    @pytest.mark.parametrize("path", ["/", "/reports", "/blitzy-unknown-page"])
    def test_pages_carry_no_component_library_markup(self, client: FlaskClient, path: str) -> None:
        """No framework class signature, and in fact no class attribute at all.

        The stylesheet styles semantic elements rather than a class taxonomy, so
        the rendered markup carries not one class attribute -- and a framework
        signature anywhere in the document, in a class name or in a URL, would mean
        a component library had been introduced where the plan forbids one.
        """
        markup = _body_of(client.get(path))
        lowered = markup.lower()

        classes = _CLASS_ATTRIBUTE_PATTERN.findall(markup)
        assert not classes, f"{path} carries {len(classes)} class attribute(s)"

        signatures = [name for name in FRAMEWORK_CLASS_SIGNATURES if name in lowered]
        assert not signatures, f"{path} carries framework signature(s) {signatures!r}"


# =============================================================================
# Requirement 2 -- the error pages, and the application-level handler trap.
# =============================================================================


class TestErrorPages:
    """404, 405 and 500 on the web surface, answered by application-level handlers.

    The handlers are registered on the application and never on a blueprint,
    because a blueprint does not own a URL space and a blueprint-scoped 404 or 405
    handler is therefore never consulted for a URL that matched no rule. Had that
    been overlooked, an unknown route would have produced the framework's default
    error page instead of the intended response -- which is why every assertion
    below reads the rendered body and not only the status code. Both statuses are
    identical whichever page answered; only the body tells them apart.

    The same handlers answer ``/api/v1`` traffic with a structured JSON envelope
    instead of a page, which is the dual shape the enterprise baseline asks for.
    That half of the contract is asserted in
    ``tests/integration/test_api_routes.py`` and is deliberately not repeated here.
    """

    def test_unknown_url_renders_the_project_404_page(self, client: FlaskClient) -> None:
        """An unmatched URL is answered by the project's own rendered 404 page."""
        response = client.get("/blitzy-unknown-page")
        markup = _body_of(response)

        assert response.status_code == HTTP_NOT_FOUND
        _assert_html_utf8(response)
        _assert_markers_present(markup, NOT_FOUND_MARKERS, "the 404 page")
        _assert_markers_present(markup, BASE_LAYOUT_MARKERS, "the layout of the 404 page")
        _assert_markers_absent(
            markup,
            FRAMEWORK_DEFAULT_ERROR_MARKERS,
            "the framework's default error page",
        )
        assert not markup.lstrip().startswith(
            "{"
        ), "the web surface answered a 404 with a JSON body instead of a rendered page"

    def test_wrong_method_is_answered_by_the_application_level_handler(
        self, client: FlaskClient
    ) -> None:
        """A wrong-method request to a real rule renders a page and advertises ``Allow``.

        ``POST /`` matches an existing rule by path and is rejected by method, so
        this exercises the 405 arm rather than the 404 one. There is deliberately no
        fifth error template: a non-API 405 reuses the 404 page, which is why the
        404 markers are the ones expected here.

        The ``Allow`` header is part of the status's contract, and a handler that
        builds its own response drops the one the exception would have carried, so
        its presence is asserted too.
        """
        response = client.post("/")
        markup = _body_of(response)

        assert response.status_code == HTTP_METHOD_NOT_ALLOWED
        _assert_html_utf8(response)
        allow = response.headers.get("Allow", "")
        assert "GET" in allow, f"the 405 response advertises {allow!r} as allowed methods"
        _assert_markers_present(markup, NOT_FOUND_MARKERS, "the 405 page")
        _assert_markers_present(markup, BASE_LAYOUT_MARKERS, "the layout of the 405 page")
        _assert_markers_absent(
            markup,
            FRAMEWORK_DEFAULT_ERROR_MARKERS,
            "the framework's default error page",
        )

    def test_no_fifth_error_template_exists_or_is_needed(self, project_root: Path) -> None:
        """The error templates are exactly ``404.html`` and ``500.html``.

        A 405 has no page of its own by design, so this asserts both halves: the
        directory holds those two files and nothing else, and the two template
        names the error module renders are precisely those two. The directory is
        located through the session-scoped repository root, which is derived from a
        module location rather than from the working directory -- every parallel
        worker is a separate process and none is guaranteed to have been started
        from the root.
        """
        errors_dir = project_root / "app" / "templates" / "errors"

        assert errors_dir.is_dir()
        assert sorted(entry.name for entry in errors_dir.iterdir()) == ["404.html", "500.html"]
        assert NOT_FOUND_TEMPLATE == "errors/404.html"
        assert INTERNAL_SERVER_ERROR_TEMPLATE == "errors/500.html"
        assert not (errors_dir / "405.html").exists()

    def test_unhandled_exception_renders_the_project_500_page(self, app: Flask) -> None:
        """An unhandled exception is answered by the project's own rendered 500 page.

        Two deliberate manoeuvres make this test exercise the handler at all.

        First, exception propagation is switched off. Flask propagates exceptions
        out of the test client while testing mode is on, which would raise the
        error into this test and never invoke the handler -- the 500 page would look
        untested while quietly never rendering. Passing the override through the
        factory disables propagation for this application only.

        Second, the raising rule is registered on a dedicated application built for
        this test alone, so no shared fixture is mutated and no raising rule
        survives the test. Both halves are asserted: the page renders, and the
        rule is absent from the application the ``app`` fixture handed over.
        """
        dedicated = create_app("testing", PROPAGATE_EXCEPTIONS=False)
        assert dedicated is not app
        assert dedicated.config["PROPAGATE_EXCEPTIONS"] is False

        rule = "/blitzy-raising-route"
        dedicated.add_url_rule(
            rule,
            endpoint="blitzy_raising_route",
            view_func=_always_raises,
            methods=["GET"],
        )

        with dedicated.test_client() as dedicated_client:
            response = dedicated_client.get(rule)
        markup = _body_of(response)

        assert response.status_code == HTTP_INTERNAL_SERVER_ERROR
        _assert_html_utf8(response)
        _assert_markers_present(markup, INTERNAL_SERVER_ERROR_MARKERS, "the 500 page")
        _assert_markers_present(markup, BASE_LAYOUT_MARKERS, "the layout of the 500 page")
        _assert_markers_absent(
            markup,
            FRAMEWORK_DEFAULT_ERROR_MARKERS,
            "the framework's default error page",
        )
        assert (
            "deliberate failure" not in markup
        ), "the 500 page echoed the exception instead of a fixed generic sentence"
        assert rule not in {
            existing.rule for existing in app.url_map.iter_rules()
        }, "the temporary raising rule leaked onto the shared application"

    def test_error_handlers_are_registered_at_application_scope(self, app: Flask) -> None:
        """Every handler sits at application scope, and none at blueprint scope.

        This is the trap the plan researched: a blueprint does not own a URL space,
        so a blueprint-scoped handler never fires for an unmatched URL. Flask keys
        application-scoped handlers under ``None``, so asserting that ``None`` is
        the only key -- and that it carries all three statuses -- is what makes the
        registration decision auditable instead of incidental.
        """
        spec = app.error_handler_spec

        assert set(spec) == {None}
        registered = spec[None]
        for status in (HTTP_NOT_FOUND, HTTP_METHOD_NOT_ALLOWED, HTTP_INTERNAL_SERVER_ERROR):
            assert status in registered, f"no application-level handler for {status}"
        for blueprint_name in app.blueprints:
            assert (
                blueprint_name not in spec
            ), f"the {blueprint_name!r} blueprint registered its own error handler"


# =============================================================================
# Requirement 3 -- the application always comes from the factory.
# =============================================================================


class TestApplicationFactoryIsolation:
    """Every application is built by the factory, and no two share state.

    The pattern is mandatory rather than stylistic here. Without it, tests could
    not build isolated application instances and fixtures would share mutable state
    across the parallel workers the ported suite runs with by default -- and that
    parallelism is itself the port of Surefire's method-level parallelism with
    unlimited threads ``[pom.xml:L22-L23]``, so the isolation is a parity
    requirement rather than a convenience.
    """

    def test_factory_yields_a_distinct_application_every_call(self, app: Flask) -> None:
        """Two calls of the factory return two independent applications.

        Independent means more than "not the same object": the configuration
        mapping, the blueprint registry and the URL map must be distinct too, or a
        mutation in one test would be visible in the next. The final assertion
        proves that directly, by mutating one configuration and checking the other
        never sees it.
        """
        second = create_app("testing")

        assert second is not app
        assert second.config is not app.config
        assert second.blueprints is not app.blueprints
        assert second.url_map is not app.url_map
        assert sorted(second.blueprints) == sorted(app.blueprints) == ["api", "web"]

        probe_key = "BLITZY_ISOLATION_PROBE"
        second.config[probe_key] = True
        assert probe_key not in app.config

    def test_client_is_bound_to_the_application_under_test(
        self, app: Flask, client: FlaskClient
    ) -> None:
        """The test client dispatches through this test's own application.

        Without this the rest of the module would be asserting against an
        application nobody can identify. It is also what makes the in-process client
        equivalent to a live server for these purposes, with no port to bind and
        none to collide over.
        """
        assert client.application is app

    def test_web_blueprint_is_mounted_at_the_root(self, app: Flask) -> None:
        """``web_bp`` carries no URL prefix, so its two rules sit at the root.

        The view functions are compared by identity against
        :mod:`app.web.routes`, which proves the rules are wired to the real views
        rather than merely existing under the expected names.
        """
        assert app.blueprints["web"].url_prefix is None

        endpoints = {rule.rule: rule.endpoint for rule in app.url_map.iter_rules()}
        assert endpoints["/"] == "web.index"
        assert endpoints["/reports"] == "web.reports"
        assert app.view_functions["web.index"] is web_routes.index
        assert app.view_functions["web.reports"] is web_routes.reports


# =============================================================================
# Requirement 4 -- the artifact layout contract, and configuration optionality.
# =============================================================================


class TestArtifactLayoutContract:
    """The five-directory layout the page reports on, and the optional config file.

    The layout is owned by :mod:`app.utils.paths` and named identically by the
    application factory, the ``Makefile`` and ``tests/conftest.py``: ``target/``,
    ``target/cucumber/``, ``target/screenshots/``, ``target/error-shots/`` and
    ``target/surefire-reports/``. Nothing here adds or drops one, and nothing here
    touches the repository's own artifact tree -- every layout the tests create is
    built inside a throwaway directory through the module's own optional base
    directory.
    """

    def test_layout_is_exactly_the_five_managed_directories(self, tmp_path: Path) -> None:
        """The layout module creates the five directories and no others.

        The set is a cross-module contract, so it is checked against the module's
        own name constants rather than against a hand-written list -- a rewritten
        expectation would defeat the purpose of the check.
        """
        expected = paths.managed_directories(tmp_path)
        created = paths.ensure_target_layout(tmp_path)

        assert created == expected
        assert len(expected) == 1 + len(paths.TARGET_SUBDIR_NAMES)
        assert expected[0] == tmp_path / paths.TARGET_DIR_NAME
        assert [directory.name for directory in expected[1:]] == list(paths.TARGET_SUBDIR_NAMES)
        for directory in expected:
            assert directory.is_dir()

        root = tmp_path / paths.TARGET_DIR_NAME
        assert sorted(entry.name for entry in root.iterdir()) == sorted(paths.TARGET_SUBDIR_NAMES)

    def test_reports_page_lists_the_whole_managed_layout(
        self,
        client: FlaskClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Each of the five managed directories, or a file inside it, is on the page.

        The artifact root itself is represented by the three report files written
        directly into it, and the four subdirectories are listed in their own right.
        Together that is the complete layout, which is what makes the page a
        faithful index of the tree rather than a partial one.
        """
        paths.ensure_target_layout(tmp_path)
        monkeypatch.chdir(tmp_path)

        markup = _body_of(client.get("/reports"))

        for report_file in paths.REPORT_FILES:
            assert paths.to_posix(report_file) in markup
        for subdirectory in paths.TARGET_SUBDIRS:
            assert paths.to_posix(subdirectory) in markup

    def test_shot_directories_come_from_the_reporting_module(self) -> None:
        """The two shot directories are the ones the reporting module indexes.

        The report page surfaces the screen shots and the error shots of
        ``[README.md:L42-L43]``, and the module that indexes them must agree with
        the module that owns the layout. Asserting the agreement here is what stops
        the page describing one directory while the indexer reads another.
        """
        assert shot_directory(ShotCategory.SCREEN_SHOTS) == paths.SCREENSHOTS_DIR
        assert shot_directory(ShotCategory.ERROR_SHOTS) == paths.ERROR_SHOTS_DIR
        assert ShotCategory.SCREEN_SHOTS.value == paths.SCREENSHOTS_DIR_NAME
        assert ShotCategory.ERROR_SHOTS.value == paths.ERROR_SHOTS_DIR_NAME

    @pytest.mark.parametrize("path", ["/", "/reports"])
    def test_pages_render_without_the_optional_runtime_configuration(
        self, client: FlaskClient, project_root: Path, path: str
    ) -> None:
        """Both pages render with the git-ignored runtime configuration absent.

        ``configuration.properties`` is git-ignored, so a fresh checkout never has
        it and every setting must fall back to a documented default; the same is
        true of ``.env``. Both views read no configuration at all, which is the
        strongest form of that guarantee, and this test proves it holds by asserting
        it while neither file exists.

        When a developer's own copy is present the pre-condition does not hold, so
        the test reports a skip with the reason rather than passing on evidence it
        did not gather.
        """
        present = [
            candidate.name
            for candidate in (project_root / "configuration.properties", project_root / ".env")
            if candidate.exists()
        ]
        if present:
            pytest.skip(
                f"{present!r} exists in this working tree, so the absent-configuration "
                "pre-condition of this test does not hold here"
            )

        response = client.get(path)

        assert response.status_code == 200
        _assert_html_utf8(response)
        _assert_markers_present(_body_of(response), BASE_LAYOUT_MARKERS, f"the layout of {path}")
