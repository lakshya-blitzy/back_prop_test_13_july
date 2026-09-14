"""Tests for the read-only viewer's HTTP surface: all six routes, and only six.

The gate for the whole HTTP contract AAP 0.3.1 calls *"the complete contract"*:
the six blueprint endpoints and their success bodies, every cause of a 404, the
artifact allowlist and its hostile-path rejections, the read-only guarantee, and
the two error handlers ``app/errors.py`` registers.

The application comes from ``tests/conftest.py``'s :fixture:`flask_app` and
:fixture:`client`, which call ``app.create_app()``, and from nowhere else: no
test builds a stand-in, registers a blueprint, installs a handler, skips or
xfails, so a missing or signature-broken factory fails every test here.  What
:fixture:`error_app` adds is strict Jinja undefined and the test-only
:data:`PROBE_RULE`, which makes the 500 handler reachable without any
production route being capable of raising.

The views call ``app.utils.paths`` accessors with no ``base``, and those read
``Path.cwd()`` per call, so the working directory *is* the artifact root a
request sees: every route test runs inside :fixture:`results_root`, which
``monkeypatch``es the cwd into an empty :fixture:`tmp_artifact_root`, undoes it
at teardown and leaves the repository's ``target/`` unread.  Results come from
``app.reporting.cucumber_json.build_cucumber_json``, so the viewer meets what
the writer emits, and the four shapes that writer cannot produce are written
directly, as data-availability causes the viewer answers identically.

Assertions are on structure and contract - names, indices, statuses, links,
content types, status codes - never on an artifact's bytes, the one exception
being the 404 bodies AAP 0.3.1 requires to be indistinguishable from each other.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Final
from urllib.parse import urljoin

import pytest
from flask import Flask, render_template, url_for
from flask.testing import FlaskClient
from jinja2 import StrictUndefined
from markupsafe import escape
from werkzeug.test import TestResponse

from app.reporting.cucumber_json import build_cucumber_json
from app.reporting.html_report import build_summary
from app.utils import (
    ARTIFACT_SPECS,
    CUCUMBER_JSON_NAME,
    CUCUMBER_JSON_RELPATH,
    CUCUMBER_REPORTS_HTML_NAME,
    RERUN_TXT_NAME,
    TARGET_DIR_NAME,
    WORKERS_DIR_NAME,
    ArtifactSpec,
    cucumber_json_path,
    open_artifact_read,
    open_resolved_artifact,
)

# --------------------------------------------------------------------------
# Fixed names: the endpoints, the templates and the test-only probe
# --------------------------------------------------------------------------

#: The template name the 500 handler resolves, and the name this suite uses.
ERROR_500_TEMPLATE: Final[str] = "errors/500.html"

#: The path of the test-only rule that provokes a handler exception.  It is
#: added to the factory-built application inside :fixture:`error_app` and
#: exists nowhere else, so no production route has to be able to fail for the
#: handler tests to run.
PROBE_RULE: Final[str] = "/blitzy-internal-error-probe"

#: Endpoint name for that rule.  Deliberately outside the ``web.`` namespace,
#: so the "exactly six rules plus ``static``" assertion below reads the
#: production surface and cannot be confused by the probe.
PROBE_ENDPOINT: Final[str] = "blitzy_internal_error_probe"

#: The six endpoints the blueprint carries, with the rules they are bound to.
#: ``app/web/__init__.py`` calls the blueprint name ``web`` a contract rather
#: than a preference and ``base.html`` builds every URL against these names, so
#: they are the names every request below is generated from.
WEB_ENDPOINT_RULES: Final[tuple[tuple[str, str], ...]] = (
    ("web.index", "/"),
    ("web.reports_overview", "/reports"),
    ("web.report_feature", "/reports/features/<int:findex>"),
    ("web.report_scenario", "/reports/features/<int:findex>/scenarios/<int:sindex>"),
    ("web.reports_summary", "/reports/summary"),
    ("web.artifact", "/artifacts/<path:name>"),
)

#: The four routes governed by the one data-availability rule, with the view
#: arguments each needs.  ``web.index`` is absent by requirement: it is the one
#: route that rule does not govern.
REPORT_ENDPOINT_ARGS: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    ("web.reports_overview", {}),
    ("web.report_feature", {"findex": 0}),
    ("web.report_scenario", {"findex": 0, "sindex": 0}),
    ("web.reports_summary", {}),
)

#: The three report routes whose body is HTML.  Their 404 bodies must be byte
#: identical to one another and across every cause; the summary route's must be
#: JSON, so it is asserted separately.
HTML_REPORT_ENDPOINT_ARGS: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    REPORT_ENDPOINT_ARGS[0],
    REPORT_ENDPOINT_ARGS[1],
    REPORT_ENDPOINT_ARGS[2],
)

#: The project's single state hook: the attribute every rendered surface
#: carries a status on, and the only one it may.  ``partials/status_badge.html``
#: states it as a contract - "THE STATUS HOOK IS data-report-status, AND IT IS
#: THE ONLY ONE" - and ``app/static/css/main.css`` and ``app/static/js/report.js``
#: both address the status through it, so every assertion below that reads a
#: status back out of markup reads this attribute rather than restating its
#: name.  The stylesheet paints from it and the script filters on it, which is
#: why a page that spelled it differently would render uncoloured and unfilterable
#: while still carrying the right words.
STATUS_HOOK: Final[str] = "data-report-status"

#: The one module outside ``app/utils`` that ``app/web/routes.py`` may reach:
#: the normalized result model AAP 0.4.2's invariant list requires every report
#: surface to render over.  Permitted by its exact name and never by the
#: ``app.reporting`` prefix, so a writer cannot arrive beside it.
PERMITTED_REPORTING_EDGE: Final[str] = "app.reporting.aggregation"

#: The report modules the viewer must never reach, each named individually
#: because the permitted edge above punches a hole in the package prefix.  The
#: event collector owns the schema and every one of the others WRITES: the four
#: artifact writers and the screenshot module, which encodes captured bytes.
FORBIDDEN_REPORTING_MODULES: Final[tuple[str, ...]] = (
    "app.reporting.events",
    "app.reporting.cucumber_json",
    "app.reporting.rerun_report",
    "app.reporting.html_report",
    "app.reporting.pretty_reports",
    "app.reporting.screenshots",
)

#: The cache policy every sensitive response must carry (CWE-525).  Report
#: pages quote the step and assertion text of a suite whose fixtures are
#: credentials, the scenario page embeds the failure screenshot, and the
#: artifact route serves the results file itself - so a copy must not outlive
#: the artifacts the clean step removes.  ``no-store`` rather than
#: ``no-cache``: RFC 9111 makes the latter a revalidation requirement, under
#: which the representation is still written to disk.
NO_STORE_POLICY: Final[str] = "no-store, private, max-age=0"

#: The HTTP/1.0 spelling that accompanies it.
NO_STORE_PRAGMA: Final[str] = "no-cache"

#: Methods no route accepts.  The surface is read-only, so each of the six
#: answers 405 for all four.
WRITE_METHODS: Final[tuple[str, ...]] = ("POST", "PUT", "PATCH", "DELETE")

#: The methods a read-only rule does accept, which is what its ``Allow`` header
#: must advertise and the whole of what it may.
READ_METHODS: Final[frozenset[str]] = frozenset({"GET", "HEAD", "OPTIONS"})


class ProbeError(RuntimeError):
    """Raised by the test-only rule to drive the 500 handler.

    Its class name and its message are both distinctive so that the
    disclosure assertions can look for them in the response body and fail if
    either leaked out of the log and into the page.
    """


#: Distinctive text carried by the provoked exception.  Deliberately free of
#: characters that HTML escaping would alter, so that finding it in the body
#: cannot be defeated by escaping and missing it cannot be an artefact of it.
PROBE_MESSAGE: Final[str] = "blitzy-probe-secret-detail-must-not-be-disclosed"

#: Flask config for the handler-path tests, applied through conftest's indirect
#: parametrization of :fixture:`flask_app`.  ``TESTING`` is turned back off and
#: propagation disabled explicitly because Flask propagates an exception out of
#: the test client when either testing or debug is on, and these tests need the
#: registered handler to answer instead.
HANDLER_APP_CONFIG: Final[dict[str, object]] = {
    "TESTING": False,
    "DEBUG": False,
    "PROPAGATE_EXCEPTIONS": False,
}

#: Applied to every test that provokes the 500 handler.  ``parametrize`` is a
#: builtin mark, so this adds no custom marker under ``--strict-markers``.
handler_app = pytest.mark.parametrize(
    "flask_app", [HANDLER_APP_CONFIG], indirect=True
)

# --------------------------------------------------------------------------
# Content fixed by the artifacts and by the system under test
# --------------------------------------------------------------------------

#: The results artifact's path inside the checkout, imported from the module
#: that owns every path in the port rather than written out here, so this
#: module cannot drift from the location the views read.
RESULTS_RELPATH: Final[str] = CUCUMBER_JSON_RELPATH

#: The French validation message ``features/Login.feature:89`` asserts, used
#: here to prove non-ASCII content survives the whole path from the writer
#: through the file to the rendered page.
FRENCH_VALIDATION_MESSAGE: Final[str] = "Veuillez renseigner ce champ."

#: Content planted in files a request must never be served, so that a rejection
#: can be proved by the absence of the bytes rather than by a status code alone.
WORKER_PAYLOAD: Final[str] = "blitzy-worker-intermediate-must-not-be-served"
OUTSIDE_PAYLOAD: Final[str] = "blitzy-outside-the-artifact-root-must-not-be-served"
UNRELATED_PAYLOAD: Final[str] = "blitzy-unlisted-artifact-must-not-be-served"

#: Content planted in each servable artifact, keyed by the name a request uses.
#: Distinct per artifact so that serving the wrong one fails.
ARTIFACT_CONTENT: Final[dict[str, str]] = {
    "cucumber-reports.html": "<html><body>the generated html report</body></html>",
    # A valid top-level list, and distinctive, so that "this file's content
    # did not reach a rejection body" is an assertion worth making.
    "cucumber.json": '[{"blitzy-results-artifact": true}]\n',
    "rerun.txt": "features/Crm.feature:9:24\n",
}

#: The report tree's overview page, which a request for the directory is served.
PRETTY_OVERVIEW_CONTENT: Final[str] = (
    "<html><body>the report tree overview page</body></html>"
)

#: One detail page under the report tree, to prove a path *beneath* it serves.
PRETTY_DETAIL_RELPATH: Final[str] = (
    "cucumber/cucumber-html-reports/report-feature_1.html"
)
PRETTY_DETAIL_CONTENT: Final[str] = "<html><body>one feature detail page</body></html>"

#: One vendored asset beneath the report tree.  Those pages have to render
#: offline from a Jenkins workspace, so their stylesheets travel with them and
#: are served through this same route - with a media type of their own, which
#: is why a non-HTML member of the subtree is part of the content-type matrix.
PRETTY_ASSET_RELPATH: Final[str] = "cucumber/cucumber-html-reports/css/cucumber.css"
PRETTY_ASSET_CONTENT: Final[str] = ".tqa-report { color: #1f2937; }\n"

#: The overview page's path under the report tree, as the path module fixes it.
PRETTY_OVERVIEW_RELPATH: Final[str] = (
    "cucumber/cucumber-html-reports/overview-features.html"
)

#: An unlisted file inside the artifact root, and a directory that is not the
#: report tree with a file inside it.  Named distinctively so that asserting
#: their absence from a response body means something.
UNLISTED_NAME: Final[str] = "blitzy-unlisted-artifact.txt"
UNRELATED_DIR_NAME: Final[str] = "blitzy-unrelated-directory"
UNRELATED_FILE_NAME: Final[str] = f"{UNRELATED_DIR_NAME}/blitzy-unrelated-file.txt"

#: A worker intermediate, which must never be reachable over HTTP.
WORKER_FILE_NAME: Final[str] = ".workers/worker-1-0000.json"

#: A symlink inside the report tree whose target lies outside the artifact
#: root - the case ``_within_artifact_root`` makes its check after ``resolve()``
#: for, and the one a purely textual allowlist cannot catch.
ESCAPING_LINK_NAME: Final[str] = "cucumber/blitzy-escaping-link.html"

#: The file that link points at, one level above the artifact root.
OUTSIDE_FILE_NAME: Final[str] = "blitzy-outside-the-root.txt"

#: Substituted into a ``url_for``-built artifact URL to send a byte-exact
#: hostile segment.  ``url_for`` quotes a backslash and double-encodes a
#: percent sequence, so a hostile representation cannot be expressed as a view
#: argument; the route's own prefix still comes from ``url_for`` against the
#: endpoint name, and only the segment is substituted.
ARTIFACT_NAME_SENTINEL: Final[str] = "blitzy-artifact-name-sentinel"

#: The two spellings of the report tree's directory AAP 0.3.1 authorizes, and
#: the longer trailing-separator variants it does not.  Written out rather than
#: derived, so a change to the path module's allowlist fails here instead of
#: quietly moving what this suite calls authorized.
TREE_DIRECTORY_SPELLINGS: Final[tuple[str, ...]] = ("cucumber", "cucumber/")
TREE_DIRECTORY_OVER_SPELLINGS: Final[tuple[str, ...]] = (
    "cucumber//",
    "cucumber///",
    "cucumber////",
)

#: A results document planted **outside** the artifact root, list-shaped so
#: that nothing but its provenance can be what a route objects to, and carrying
#: a canary that no response may ever contain.  Reached through a symbolic link
#: and through a hard link at the results path, which is the pair of
#: arrangements a pathname read follows and a verified descriptor refuses.
EXTERNAL_RESULTS_NAME: Final[str] = "blitzy-external-results.json"
EXTERNAL_RESULTS_CANARY: Final[str] = "blitzy-external-results-canary"

#: Planted in a file the artifact route has already validated, to be swapped in
#: after validation: what the response carries decides whether the route serves
#: the object it checked or whatever the pathname names by the time the bytes
#: are read.
VALIDATED_ARTIFACT_CONTENT: Final[str] = "blitzy-the-artifact-that-was-validated"
SWAPPED_ARTIFACT_CONTENT: Final[str] = "blitzy-swapped-in-after-validation"


# --------------------------------------------------------------------------
# URL construction.  Every request below addresses an endpoint name, never a
# rule written out by hand: the names are the contract the templates build
# against, so a renamed view has to fail here.
# --------------------------------------------------------------------------


def _url(flask_app: Flask, endpoint: str, **values: Any) -> str:
    """Build one URL with ``url_for``, against an endpoint name.

    A request context is pushed for the duration of the call because that is
    what ``url_for`` resolves against; nothing else about the context is used,
    and the request the test then makes is a separate one.

    :param flask_app: The application whose URL map answers.
    :param endpoint: An endpoint name, e.g. ``web.report_feature``.
    :param values: View arguments for the rule.
    :returns: The path to request.
    """
    with flask_app.test_request_context("/"):
        return url_for(endpoint, **values)


def _artifact_url(flask_app: Flask, raw_name: str) -> str:
    """An artifact URL carrying ``raw_name`` exactly as written.

    For the hostile representations: the prefix is still built by ``url_for``
    against ``web.artifact``, and the sentinel segment is replaced with the raw
    text, so the test sends the bytes it intends rather than whatever quoting
    would make of them.

    :param flask_app: The application whose URL map answers.
    :param raw_name: The segment to place after the artifact prefix.
    :returns: The path to request.
    """
    template = _url(flask_app, "web.artifact", name=ARTIFACT_NAME_SENTINEL)
    return template.replace(ARTIFACT_NAME_SENTINEL, raw_name)


def _every_route_url(flask_app: Flask) -> tuple[str, ...]:
    """One URL per production route, in the order of the AAP's route table.

    The artifact route is addressed by the results artifact's own key, which is
    allowlisted, so the tuple exercises all six rules rather than five and a
    rejection.

    :param flask_app: The application whose URL map answers.
    :returns: Six paths, one per endpoint.
    """
    return (
        _url(flask_app, "web.index"),
        _url(flask_app, "web.reports_overview"),
        _url(flask_app, "web.report_feature", findex=0),
        _url(flask_app, "web.report_scenario", findex=0, sindex=0),
        _url(flask_app, "web.reports_summary"),
        _url(flask_app, "web.artifact", name="cucumber.json"),
    )


# --------------------------------------------------------------------------
# Building the results artifact.  Documents come from the production writer,
# so the viewer is tested against exactly the shape a run leaves behind.
# --------------------------------------------------------------------------


def _step(
    name: str,
    status: str,
    *,
    keyword: str = "Given ",
    line: int = 3,
    duration: int | None = 1_000_000,
) -> dict[str, Any]:
    """One internal-schema step.

    :param name: The step text, without its keyword.
    :param status: The step's outcome, one of the result model's seven.
    :param keyword: The Gherkin keyword, trailing space included as the
        artifact carries it.
    :param line: The step's line in its feature file.
    :param duration: Nanoseconds, or ``None`` for a step the engine never
        timed - a skipped step carries no duration key at all.
    :returns: The step mapping, ready for the writer.
    """
    result: dict[str, Any] = {"status": status}
    if duration is not None:
        result["duration"] = duration
    return {
        "keyword": keyword,
        "line": line,
        "name": name,
        "matched": True,
        "match": {"location": "features.steps.login_steps.a_step"},
        "result": result,
    }


def _scenario(
    name: str,
    *,
    steps: list[dict[str, Any]],
    line: int = 5,
    started: str | None = None,
) -> dict[str, Any]:
    """One internal-schema scenario element.

    :param name: The scenario name, which the viewer renders and links by.
    :param steps: Its steps, in file order.
    :param line: Its line in the feature file.
    :param started: Its ``start_timestamp``, in the millisecond-precision UTC
        form ending in ``Z`` that the writer emits, or ``None``.
    :returns: The element mapping, ready for the writer.
    """
    return {
        "type": "scenario",
        "keyword": "Scenario",
        "line": line,
        "name": name,
        "description": "",
        "steps": steps,
        "start_timestamp": started,
    }


def _background(
    *,
    steps: list[dict[str, Any]],
    name: str = "shared setup",
    line: int = 3,
) -> dict[str, Any]:
    """One internal-schema background element.

    A background carries no ``id``, no ``tags`` and no ``start_timestamp``, and
    the writer strips any it were given, so none is supplied here.

    :param steps: Its steps, in file order.
    :param name: Its name, as the feature file writes it.
    :param line: Its line in the feature file.
    :returns: The element mapping, ready for the writer.
    """
    return {
        "type": "background",
        "keyword": "Background",
        "line": line,
        "name": name,
        "description": "",
        "steps": steps,
    }


def _feature(
    name: str,
    relpath: str,
    elements: list[dict[str, Any]],
    *,
    line: int = 1,
) -> dict[str, Any]:
    """One internal-schema feature.

    No ``id`` is supplied: the writer derives one from the name with the JVM's
    own slug rule, which is what makes the two title collisions reproducible
    here rather than asserted against a hand-typed slug.

    :param name: The feature's title, as its file writes it.
    :param relpath: Its path inside the checkout, which becomes its ``uri``.
    :param elements: Its elements, backgrounds and scenarios interleaved in
        file order.
    :param line: The ``Feature:`` line.
    :returns: The feature mapping, ready for the writer.
    """
    return {
        "uri": f"file:{relpath}",
        "path": relpath,
        "keyword": "Feature",
        "line": line,
        "name": name,
        "description": "",
        "tags": [],
        "elements": elements,
    }


def _results_document(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run internal-schema features through the production JSON writer.

    :param features: Internal-schema features, in the order a merge produced.
    :returns: The Cucumber-JVM document ``target/cucumber.json`` would carry.
    """
    return build_cucumber_json(
        {
            "schema_version": 1,
            "started_at": "2022-09-07T13:00:00.000Z",
            "generated_at": "2022-09-07T13:40:00.000Z",
            "dry_run": False,
            "tag_expression": "",
            "metadata": {},
            "features": features,
        }
    )


def _linked_artifact_name(spec: ArtifactSpec) -> str:
    """The artifact-route name the landing page must link for one spec.

    The three single-file artifacts are linked by their own allowlisted key.
    The report tree is linked by the overview **page** inside it, because the
    route answers the tree's directory spellings with a redirect to that page
    and a page reached at the directory alias would resolve its relative
    references one level too high.

    :param spec: One member of ``ARTIFACT_SPECS``.
    :returns: The value the page must pass as ``web.artifact``'s ``name``.
    """
    return PRETTY_OVERVIEW_RELPATH if spec.is_dir else spec.key


def _external_results_document() -> list[dict[str, Any]]:
    """A valid results document carrying :data:`EXTERNAL_RESULTS_CANARY`.

    Produced by the writer, so the only thing wrong with it is where it lives:
    a route that refuses it is refusing its provenance and not its shape, and a
    route that renders it puts the canary in a response.

    :returns: The Cucumber-JVM document, as a run would have written it.
    """
    return _results_document(
        [
            _feature(
                EXTERNAL_RESULTS_CANARY,
                "features/External.feature",
                [
                    _scenario(
                        EXTERNAL_RESULTS_CANARY,
                        steps=[_step(EXTERNAL_RESULTS_CANARY, "passed")],
                        started="2022-09-07T13:37:26.297Z",
                    )
                ],
            )
        ]
    )


def _results_path(root: Path) -> Path:
    """Where the results artifact belongs inside one artifact root.

    :param root: The directory standing in for a checkout.
    :returns: The path of ``target/cucumber.json`` under it.
    """
    return root / RESULTS_RELPATH


def _write_results(root: Path, document: Any) -> Path:
    """Write one results document, creating the artifact root's ``target/``.

    ``ensure_ascii=False`` so a non-ASCII value reaches the file as characters,
    which is the byte shape the writer produces and what makes the French
    validation message a real round trip rather than an escape sequence.

    :param root: The directory standing in for a checkout.
    :param document: Any JSON-serialisable document, including a deliberately
        invalid one such as a mapping where the contract fixes a list.
    :returns: The path written.
    """
    path = _results_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The unusable-results states.  Five causes the viewer must not tell apart,
# arranged here so that the assertions read as one rule with five inputs.
#
# "Unreadable" is a directory standing where the file belongs and a file of
# undecodable bytes, deliberately: this suite runs as root, where the mode bits
# do not deny a read, so a chmod-based case would pass while proving nothing.
# --------------------------------------------------------------------------

#: The five causes, each of which must be indistinguishable from the others.
UNUSABLE_RESULTS_CAUSES: Final[tuple[str, ...]] = (
    "absent",
    "directory",
    "undecodable",
    "unparseable",
    "not-a-list",
)


def _install_unusable_results(root: Path, cause: str) -> None:
    """Put the results artifact into one unusable state, replacing any other.

    :param root: The artifact root to arrange.
    :param cause: One of :data:`UNUSABLE_RESULTS_CAUSES`.
    :raises AssertionError: If the cause is not one of the five, so that a
        mistyped parametrization fails loudly instead of testing nothing.
    """
    path = _results_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        path.rmdir()
    elif path.exists():
        path.unlink()

    if cause == "absent":
        return
    if cause == "directory":
        path.mkdir()
        return
    if cause == "undecodable":
        # Not valid UTF-8 anywhere in the file, so the read fails before the
        # parse does - the "unreadable" cause, arranged so root cannot defeat
        # it.
        path.write_bytes(b"\xff\xfe\x00 not utf-8 at all")
        return
    if cause == "unparseable":
        path.write_text("{not json", encoding="utf-8")
        return
    if cause == "not-a-list":
        # Parses cleanly and describes no run: the contract pins the top level
        # as a list of feature objects.
        path.write_text('{"features": []}', encoding="utf-8")
        return
    raise AssertionError(f"unknown unusable-results cause: {cause!r}")


# --------------------------------------------------------------------------
# Foreign provenance.  Two ways a file outside the artifact root can answer at
# the results path: a symbolic link, which a pathname read follows, and a hard
# link, which no symlink check can even see.  Both are the same state as far as
# the viewer is concerned - the thing at that path is not what a run wrote -
# and both must be refused by every route that reads it.
# --------------------------------------------------------------------------

#: The two arrangements, each of which must be indistinguishable from an absent
#: artifact in every response.
FOREIGN_RESULTS_ARRANGEMENTS: Final[tuple[str, ...]] = ("symlink", "hardlink")


def _install_foreign_results(root: Path, arrangement: str) -> Path:
    """Make an external results document answer at the results path.

    The external file is written **above** the artifact root and carries a
    perfectly valid, list-shaped document, so nothing about its content can be
    the reason a route refuses it.

    :param root: The artifact root to arrange.
    :param arrangement: One of :data:`FOREIGN_RESULTS_ARRANGEMENTS`.
    :returns: The external file's path, so a test can prove it is readable and
        valid before asserting that no response contains it.
    :raises AssertionError: If the arrangement is not one of the two, so that a
        mistyped parametrization fails loudly instead of testing nothing.
    """
    external = root / EXTERNAL_RESULTS_NAME
    external.write_text(
        json.dumps(_external_results_document(), ensure_ascii=False),
        encoding="utf-8",
    )
    path = _results_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        path.rmdir()
    elif path.exists() or path.is_symlink():
        path.unlink()

    if arrangement == "symlink":
        os.symlink(external, path)
        assert path.is_symlink()
        return external
    if arrangement == "hardlink":
        os.link(external, path)
        assert not path.is_symlink()
        assert path.stat().st_nlink == 2, (
            "the hard link was not created, so the case would prove nothing"
        )
        return external
    raise AssertionError(f"unknown foreign-results arrangement: {arrangement!r}")


# --------------------------------------------------------------------------
# Reading a response
# --------------------------------------------------------------------------


def _main_region(html: str) -> str:
    """The markup inside ``<main>`` - the region an extending template owns.

    Several structural assertions have to be scoped this way rather than run
    over the whole document, and stating why keeps them from being vacuous:
    base.html itself emits the deferred ``report.js`` tag, the stylesheet link
    and one behaviour hook on the ``main`` element.  Those belong to the shell.
    What a page contributes is exactly what lies between the tags below.

    :param html: A whole rendered document.
    :returns: The contents of its ``main`` element.
    """
    match = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.DOTALL | re.IGNORECASE)
    assert match is not None, "the shell did not render a main region"
    return match.group(1)


def _visible_text(html: str) -> str:
    """Text a reader sees: markup removed, entities left as written.

    :param html: A whole rendered document.
    :returns: Its visible text, with the head and every script element
        removed, so a URL or a class name cannot satisfy a disclosure check.
    """
    without_head = re.sub(r"<head\b.*?</head>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    without_script = re.sub(
        r"<script\b.*?</script>", " ", without_head, flags=re.DOTALL | re.IGNORECASE
    )
    return re.sub(r"<[^>]+>", " ", without_script)


def _attribute_values(html: str, *, excluding: frozenset[str]) -> list[str]:
    """Every quoted attribute value in ``html`` except the named attributes.

    :param html: Markup to scan.
    :param excluding: Attribute names, lower case, to leave out.
    :returns: The remaining attribute values, in document order.
    """
    return [
        value
        for name, value in re.findall(r'([a-zA-Z-]+)="([^"]*)"', html)
        if name.lower() not in excluding
    ]


def _hrefs(html: str) -> list[str]:
    """Every ``href`` value in document order.

    :param html: Markup to scan.
    :returns: The link targets, duplicates kept, so an assertion can count.
    """
    return re.findall(r'href="([^"]*)"', html)


def _escaped(text: str) -> str:
    """One artifact value as the autoescaping templates render it.

    The feature, scenario and step names in this suite carry double quotes and
    apostrophes - ``... from "CRM" module``, ``any user's information`` - and
    Jinja's autoescaping turns those into entities, so a raw substring search
    for such a name silently passes on a prefix and fails on the whole string.
    Every assertion about rendered artifact text goes through this, using the
    same escaping the templates use rather than a second implementation of it.

    :param text: A value read from the results document.
    :returns: Its rendered form.
    """
    return str(escape(text))


def _step_rows(html: str) -> list[tuple[str, str, str]]:
    """Every step row a report page rendered, in document order.

    Read from the markup the shared ``partials/step_row.html`` emits, which is
    what puts the status, the keyword and the name of one step in one place, so
    an assertion can pin a step's three facts together instead of finding three
    strings somewhere in the page.

    Three things about that markup the pattern has to allow for, because each
    is the partial's own contract rather than an accident of one caller:

    * the status travels as ``data-report-status`` - :data:`STATUS_HOOK`, the
      project's single state hook, which every template and the report script
      address the status by;
    * the row and both spans may carry *additional* class names, since the
      partial takes per-surface class overrides and the viewer pages use them;
    * the row may carry ``data-report-filterable`` after the status, which is
      what the overview's filter selects on.

    :param html: A rendered scenario or feature page.
    :returns: One ``(status, keyword, rendered name)`` triple per step row.
    :raises AssertionError: If the page carries step rows this pattern does not
        recognise.  Without that guard a drift in the partial's markup makes
        every caller compare its expectation against an empty list, which
        reports the step names as missing from a page that in fact rendered
        them - the failure is then about the parser rather than about the page,
        and says so here instead.
    """
    rows = [
        (status, keyword.strip(), name.strip())
        for status, keyword, name in re.findall(
            rf'<div class="tqa-step(?:\s[^"]*)?" {STATUS_HOOK}="([a-z]+)"[^>]*>\s*'
            r'<span class="tqa-step-keyword(?:\s[^"]*)?">(.*?)</span>\s*'
            r'<span class="tqa-step-name(?:\s[^"]*)?">(.*?)</span>',
            html,
            re.DOTALL,
        )
    ]
    assert rows or 'class="tqa-step' not in html, (
        "the page renders step rows in markup this helper does not recognise; "
        "app/templates/partials/step_row.html has changed shape"
    )
    return rows


def _holds_run_of(
    rows: list[tuple[str, str, str]], expected: list[tuple[str, str, str]]
) -> bool:
    """Whether ``expected`` occurs in ``rows`` contiguously and in order.

    Contiguity matters: a scenario's steps are one block on the page, so a
    template that interleaved another element's steps among them would be a
    real defect and must not satisfy the assertion.

    :param rows: The rows the page rendered.
    :param expected: The rows one element's steps must produce.
    :returns: ``True`` if the block occurs as written.
    """
    span = len(expected)
    return any(
        rows[start : start + span] == expected
        for start in range(len(rows) - span + 1)
    )


def _text_of(response: TestResponse) -> str:
    """A response body as text, decoded as the contract says it is encoded.

    Decoding explicitly rather than through the framework's helper is what
    makes the non-ASCII assertion meaningful: it proves the bytes on the wire
    are UTF-8, not merely that the framework can hand back a string.

    :param response: A test-client response.
    :returns: The body, decoded as UTF-8.
    """
    return response.data.decode("utf-8")


def _tree_snapshot(root: Path) -> dict[str, tuple[bool, int, int]]:
    """Every path under ``root``, with the facts a write would change.

    :param root: The directory to walk.
    :returns: A mapping of relative path to ``(is_dir, size, mtime_ns)``.
        Symlinks are recorded without being followed, so a link replaced by a
        real file is a visible difference.
    """
    snapshot: dict[str, tuple[bool, int, int]] = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        snapshot[str(path.relative_to(root))] = (
            path.is_dir() and not path.is_symlink(),
            info.st_size,
            info.st_mtime_ns,
        )
    return snapshot


# --------------------------------------------------------------------------
# Shared assertions for the two responses the contract fixes exactly
# --------------------------------------------------------------------------


def _not_found_page(client: FlaskClient, flask_app: Flask) -> bytes:
    """The canonical 404 body, obtained from a production route.

    Taken from the feature route at an index no run can have produced, which is
    one of the ten causes the handler must answer identically.  Every rejection
    assertion below compares against this rather than against a status code
    alone, so a response that happened to be a 404 for a different reason - a
    framework redirect, a different template, a leaked detail - fails.

    :param client: The test client to request with.
    :param flask_app: The application whose URL map answers.
    :returns: The body bytes of that 404.
    """
    control = client.get(
        _url(flask_app, "web.report_feature", findex=2**31 - 1)
    )
    assert control.status_code == 404
    return control.data


def _assert_rejected(
    response: TestResponse,
    *,
    canonical: bytes,
    forbidden: tuple[str, ...] = (),
) -> None:
    """Assert one artifact rejection: 404, the plain page, and no disclosure.

    Four things are checked together because each of them is a way this route
    could fail while still answering 404: the status must be 404 and never 403
    or 500, the body must be the same page every other cause renders, no
    filesystem path may appear in it, and none of the content the request tried
    to reach may appear either.

    :param response: The rejected response.
    :param canonical: The body :func:`_not_found_page` returned.
    :param forbidden: Strings that must not appear in the body - typically the
        planted content of the file the request aimed at, and the rejected name
        itself.
    :raises AssertionError: If any of the four holds false.
    """
    assert response.status_code == 404, (
        f"expected a plain 404, got {response.status_code}"
    )
    assert response.data == canonical, "the rejection did not render the 404 page"

    body = _text_of(response)
    assert not re.search(r"(?<![a-zA-Z])/(?:home|root|tmp|usr|var|opt|etc)/", body)
    assert not re.search(r"[A-Za-z]:\\", body)
    for value in forbidden:
        assert value not in body, f"the rejection body discloses {value!r}"


def _assert_read_only_methods(client: FlaskClient, url: str) -> None:
    """Assert one URL answers 405 to every write method, advertising GET only.

    :param client: The test client to request with.
    :param url: The URL to probe.
    :raises AssertionError: If a write method is accepted, or if the ``Allow``
        header advertises a method outside :data:`READ_METHODS`, which is the
        whole of what a read-only surface may offer.
    """
    for method in WRITE_METHODS:
        response = client.open(url, method=method)
        assert response.status_code == 405, (
            f"{method} {url} answered {response.status_code}, not 405"
        )
        allowed = {
            value.strip().upper()
            for value in (response.headers.get("Allow") or "").split(",")
            if value.strip()
        }
        assert allowed <= READ_METHODS, (
            f"{method} {url} advertises unexpected methods: {sorted(allowed)}"
        )
        assert "GET" in allowed, f"{method} {url} does not advertise GET"


# --------------------------------------------------------------------------
# Fixtures.  Applications come from conftest; these add the artifact root the
# views read and the test-only rule the 500 handler needs.
# --------------------------------------------------------------------------


@pytest.fixture(name="results_root")
def _results_root(tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty artifact root, made the working directory for the test.

    This is the data seam for every route test: the views call
    ``app.utils.paths`` accessors with no ``base``, and those read
    ``Path.cwd()`` fresh on every call, so the working directory is the root a
    request reads.  ``monkeypatch.chdir`` undoes itself at teardown, so no
    test leaks a directory onto the next one and the repository's own
    ``target/`` is never touched.

    Nothing is created inside: ``target/`` is absent until a test writes it,
    which is what makes "200 on a completely empty workspace" assertable.

    :param tmp_artifact_root: conftest's private empty directory.
    :param monkeypatch: pytest's patcher, for its guaranteed teardown.
    :returns: The directory, which is also now the working directory.
    """
    monkeypatch.chdir(tmp_artifact_root)
    return tmp_artifact_root


@pytest.fixture(name="sample_document")
def _sample_document(sample_result_set: Any) -> list[dict[str, Any]]:
    """The results document the writer produces from conftest's sample run.

    Measured shape, which the assertions below rely on: four features, the
    first and third sharing the id ``testinium-app-inventory-feature`` because
    they share a title, the second carrying four scenarios each preceded by its
    own Background occurrence, and one failing scenario carrying a screenshot
    embedding on its teardown hook.

    :param sample_result_set: conftest's parsed internal result set.
    :returns: The Cucumber-JVM document for it.
    """
    return build_cucumber_json(sample_result_set)


@pytest.fixture(name="artifact_tree")
def _artifact_tree(results_root: Path) -> Path:
    """An artifact root carrying every servable and unservable path at once.

    One description of a populated workspace, shared by the three sections
    that need one: the landing page lists the four artifacts it holds, the
    artifact route serves three files and a subtree out of it, and the
    read-only tests exercise the whole surface against it.

    Populated rather than empty so that each rejection test runs against a
    tree where the thing it asks for genuinely exists: a 404 for a file that
    was never there would prove nothing about the allowlist, the worker
    rejection or the containment check.  So besides the four artifacts it
    holds a populated ``.workers/``, an unlisted file, a directory that is not
    the report tree with a file inside it, and a symlink inside the report
    tree whose target sits above the artifact root.

    :param results_root: The artifact root, already the working directory.
    :returns: The same root, populated.
    """
    # The names below are written out deliberately - they are the allowlist as
    # AAP 0.3.1 states it, and a test that derived them from the same constants
    # the route uses could not catch a rename.  This is the one assertion that
    # ties the two together, so a rename fails here rather than silently
    # turning every rejection test into a test of a name nothing serves.
    assert set(ARTIFACT_CONTENT) == {
        spec.key for spec in ARTIFACT_SPECS if not spec.is_dir
    }
    assert [spec.key for spec in ARTIFACT_SPECS if spec.is_dir] == ["cucumber"]

    target = results_root / "target"
    (target / PRETTY_OVERVIEW_RELPATH).parent.mkdir(parents=True)
    for name, content in ARTIFACT_CONTENT.items():
        (target / name).write_text(content, encoding="utf-8")
    (target / PRETTY_OVERVIEW_RELPATH).write_text(
        PRETTY_OVERVIEW_CONTENT, encoding="utf-8"
    )
    (target / PRETTY_DETAIL_RELPATH).write_text(
        PRETTY_DETAIL_CONTENT, encoding="utf-8"
    )
    asset = target / PRETTY_ASSET_RELPATH
    asset.parent.mkdir(parents=True)
    asset.write_text(PRETTY_ASSET_CONTENT, encoding="utf-8")

    (target / ".workers").mkdir()
    (target / WORKER_FILE_NAME).write_text(WORKER_PAYLOAD, encoding="utf-8")

    (target / UNLISTED_NAME).write_text(UNRELATED_PAYLOAD, encoding="utf-8")
    (target / UNRELATED_DIR_NAME).mkdir()
    (target / UNRELATED_FILE_NAME).write_text(UNRELATED_PAYLOAD, encoding="utf-8")

    outside = results_root / OUTSIDE_FILE_NAME
    outside.write_text(OUTSIDE_PAYLOAD, encoding="utf-8")
    os.symlink(outside, target / ESCAPING_LINK_NAME)
    return results_root


@pytest.fixture(name="error_app")
def _error_app(flask_app: Flask) -> Flask:
    """The factory-built application, with the 500 handler made reachable.

    Two things are added to the application conftest built, and nothing else:
    strict undefined on the Jinja environment, so that a variable reference in
    the error page - or in the shell it extends - raises here instead of
    rendering as an empty string, and one test-only rule that raises.  The
    handler under test is the real one ``app/errors.py`` registered.

    :param flask_app: conftest's ``create_app()``-built application,
        parametrized by :data:`handler_app` so exceptions reach the handler.
    :returns: The same application, with the probe rule bound.
    """
    flask_app.jinja_env.undefined = StrictUndefined

    def _raise() -> str:
        raise ProbeError(PROBE_MESSAGE)

    flask_app.add_url_rule(PROBE_RULE, PROBE_ENDPOINT, _raise)
    return flask_app


@pytest.fixture(name="error_client")
def _error_client(error_app: Flask) -> Iterator[FlaskClient]:
    """A client for :fixture:`error_app`, with its context torn down.

    Entered as a context manager for the same reason conftest's own client
    fixture is: the last request's contexts stay alive for the test's
    assertions and are popped afterwards even when it fails.

    :param error_app: The application carrying the probe rule.
    :yields: A client bound to it.
    """
    with error_app.test_client() as test_client:
        yield test_client


def _error_response_body(client: FlaskClient) -> str:
    """Request the probe rule and return the rendered error page.

    :param client: A client bound to :fixture:`error_app`.
    :returns: The response body, which must be the 500 page.
    :raises AssertionError: If the response is not a 500.
    """
    response = client.get(PROBE_RULE)
    assert response.status_code == 500
    return _text_of(response)


# --------------------------------------------------------------------------
# errors/500.html - renders in isolation
# --------------------------------------------------------------------------


@handler_app
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


@handler_app
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


@handler_app
def test_error_500_handler_answers_with_the_page_and_status_500(
    error_client: FlaskClient,
) -> None:
    """The registered handler answers the page, not a framework traceback."""
    body = _error_response_body(error_client)

    assert "Internal server error" in body
    assert "tqa-empty" in body, "the message box class is missing"


@handler_app
def test_error_500_response_carries_no_exception_detail(
    error_client: FlaskClient,
) -> None:
    """No traceback, no frame, no exception class and no exception message.

    The traceback markers are matched as patterns rather than as bare words on
    purpose.  A plain search for the word "line" would match the shell's own
    footer sentence about starting runs from the command line, which would make
    the assertion fail for a reason that has nothing to do with disclosure; the
    frame pattern below is what a traceback actually looks like.
    """
    body = _error_response_body(error_client)

    assert "Traceback" not in body
    assert "Exception" not in body
    assert not re.search(r'File "[^"]*", line \d+', body)
    assert not re.search(r"\bline \d+", body)
    assert ProbeError.__name__ not in body
    assert PROBE_MESSAGE not in body
    assert "RuntimeError" not in body


@handler_app
def test_error_500_traceback_is_logged_and_absent_from_the_response(
    error_client: FlaskClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both halves of the contract, asserted together.

    The diagnostic has to exist, and it has to exist in the log rather than in
    the page.  The log is read through the runner's own capture rather than
    through the error stream, for a reason worth recording: the runner installs
    a handler on the root logger, and the record propagates to it, so this
    assertion is about the record's existence and content.  Which stream it
    lands on is asserted separately, in the test below this one.
    """
    with caplog.at_level("ERROR"):
        body = _error_response_body(error_client)

    assert "Traceback" in caplog.text
    assert PROBE_MESSAGE in caplog.text
    assert PROBE_MESSAGE not in body
    assert "Traceback" not in body


@handler_app
def test_error_500_traceback_reaches_the_error_stream(
    error_client: FlaskClient,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The traceback lands on the error stream, and only there.

    This is the half of the contract that belongs to ``app/errors.py`` and the
    handler split ``app/logging_config.py`` installs from inside
    ``create_app()``, which routes WARNING and above to the error stream.  It
    is asserted against the factory-built application because that is the only
    application this module has: there is no stand-in, so a factory that
    stopped configuring logging fails here rather than skipping.  Capture is at
    file-descriptor level so that a handler holding a reference to the original
    stream is captured as well as one resolved per request.
    """
    body = _error_response_body(error_client)
    captured = capfd.readouterr()

    assert "Traceback" in captured.err
    assert PROBE_MESSAGE in captured.err
    assert PROBE_MESSAGE not in captured.out
    assert PROBE_MESSAGE not in body
    assert "Traceback" not in body


@handler_app
def test_error_500_handler_is_registered_by_the_factory(
    error_app: Flask,
    error_client: FlaskClient,
) -> None:
    """The factory itself wires the handler, and the wiring is what answers.

    Asserted twice over, because either half alone could hold while the other
    was broken: the handler map the factory built carries an entry for 500, and
    a request that raises is answered by it with the rendered page.
    """
    registered = error_app.error_handler_spec[None][500]
    assert registered, "create_app() registered no 500 handler"

    body = _error_response_body(error_client)
    assert "Internal server error" in body


# --------------------------------------------------------------------------
# errors/500.html - information disclosure
# --------------------------------------------------------------------------


@handler_app
def test_error_500_discloses_no_configuration_or_filesystem_detail(
    error_app: Flask,
    error_client: FlaskClient,
) -> None:
    """Nothing about configuration, paths, versions or the host.

    Scope, stated so the assertion cannot be vacuous: the checks run over the
    page's visible text and over the region this template owns with the ``href``
    attribute excluded.  One href is required -- the link ``url_for`` builds to
    the index route -- and it is allowlisted by exactly its own value rather
    than by ignoring hrefs in general.  The shell's stylesheet and script URLs
    live in the head and after the body's content and belong to the shell.
    """
    body = _error_response_body(error_client)
    region = _main_region(body)
    text = _visible_text(body)

    allowed_href = _url(error_app, "web.index")

    hrefs = _hrefs(region)
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


@handler_app
def test_error_500_emits_no_link_opening_attribute(
    error_client: FlaskClient,
) -> None:
    """No attribute that opens a link elsewhere, anywhere in the document."""
    body = _error_response_body(error_client)

    assert not re.search(r"\btarget\s*=", body, re.IGNORECASE)


@handler_app
def test_error_500_echoes_nothing_the_caller_supplied(
    error_client: FlaskClient,
) -> None:
    """Nothing the request carried comes back in the page.

    The page is static, so this cannot fail by construction -- which is the
    point of asserting it: a later edit that reached for the request object to
    be helpful would fail here.  The probe value is pushed through the query
    string, a header and the user agent, and the page is compared byte for byte
    with the one served for a bare request.
    """
    echo = "blitzy-request-echo-probe-value"

    plain = error_client.get(PROBE_RULE)
    decorated = error_client.get(
        f"{PROBE_RULE}?leak={echo}",
        headers={"X-Blitzy-Probe": echo, "Referer": f"https://{echo}.invalid/"},
        environ_overrides={"HTTP_USER_AGENT": echo},
    )

    assert plain.status_code == 500
    assert decorated.status_code == 500
    plain_body = _text_of(plain)
    decorated_body = _text_of(decorated)
    assert echo not in decorated_body
    assert decorated_body == plain_body


# --------------------------------------------------------------------------
# errors/500.html - structure
# --------------------------------------------------------------------------


@handler_app
def test_error_500_emits_no_inline_behaviour_styling_or_submission(
    error_client: FlaskClient,
) -> None:
    """No inline script, no style element, no form, and no external URL.

    Scope, again stated: the shell emits one deferred script element with a
    ``src``, so the page-wide assertion is that no script element carries
    inline code and that the region this template owns carries no script element
    at all.  Style elements, forms and absolute URLs are absent document-wide.
    """
    body = _error_response_body(error_client)
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


@handler_app
def test_error_500_emits_no_state_or_behaviour_hook_of_its_own(
    error_client: FlaskClient,
) -> None:
    """State comes from absence; the shell owns the one behaviour hook.

    The three boolean state attributes mean expanded, visible and closed when
    absent, and the behaviour script sets its own hooks at run time, so server
    output carries none of them.  ``data-report-root`` on the ``main`` element is
    the shell's, which is why this is scoped to the owned region.
    """
    body = _error_response_body(error_client)
    region = _main_region(body)

    for attribute in ("data-tqa-collapsed", "data-tqa-filtered", "data-tqa-open"):
        assert attribute not in body

    assert not re.search(r"\bdata-report-[a-z-]+", region)
    assert STATUS_HOOK not in region
    # The report stylesheet's monospace failure-text treatment must not dress
    # this page, which is forbidden from carrying failure text at all.
    assert "tqa-error" not in body


@handler_app
def test_error_500_marks_no_navigation_item_as_current(
    error_client: FlaskClient,
) -> None:
    """The proof that the active-navigation variable was left unset."""
    body = _error_response_body(error_client)

    assert 'aria-current="page"' not in body
    assert "aria-current" not in body


@handler_app
def test_error_500_uses_only_declared_stylesheet_class_names(
    error_app: Flask,
    error_client: FlaskClient,
) -> None:
    """Every class in the owned region is one the stylesheet declares.

    The stylesheet is read through the application's own static folder rather
    than through a path this module computes, so the file asserted against is
    the one the application would serve.
    """
    region = _main_region(_error_response_body(error_client))
    assert error_app.static_folder is not None
    stylesheet = (Path(error_app.static_folder) / "css" / "main.css").read_text(
        encoding="utf-8"
    )
    declared = set(re.findall(r"\.(tqa-[a-z0-9-]+)", stylesheet))

    used: set[str] = set()
    for value in _attribute_values(region, excluding=frozenset({"href"})):
        used.update(token for token in value.split() if token.startswith("tqa-"))

    assert used, "the region carries no namespaced class at all"
    assert used <= declared, f"undeclared class names: {sorted(used - declared)}"
    assert "tqa-empty" in used
    assert "tqa-link" in used


@handler_app
def test_error_500_body_is_identical_across_repeated_requests(
    error_client: FlaskClient,
) -> None:
    """A fully static page cannot vary between two identical requests."""
    first = _error_response_body(error_client)
    second = _error_response_body(error_client)

    assert first == second


# --------------------------------------------------------------------------
# GET / - the landing page, the one route the data-availability rule does not
# govern.  It must be useful before anything has ever run.
# --------------------------------------------------------------------------


def _index_rows(html: str) -> list[str]:
    """The artifact list items of the landing page, in document order.

    The opening tag is kept, because the row's state is carried on it: the
    missing variant is a class on the ``li`` element itself.

    :param html: The rendered landing page.
    :returns: One whole ``li`` element per row, which is what lets an
        assertion speak about a particular artifact's row rather than about the
        page as a whole.
    """
    return re.findall(
        r'(<li class="tqa-artifact[^"]*">.*?</li>)', html, re.DOTALL | re.IGNORECASE
    )


def test_index_answers_200_on_a_completely_empty_workspace(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """200 with no build output at all, which is the first-run state.

    Asserted on a root where ``target/`` has never existed - the check below
    proves that rather than assuming it - because that is the state a fresh
    checkout is in and the landing page has to be useful in it.  The page must
    report four absent artifacts, offer no link that is certain to 404, and
    render the guidance box that tells a reader how to produce them.
    """
    assert not (results_root / "target").exists(), "the root was not empty"

    response = client.get(_url(flask_app, "web.index"))

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/html")
    body = _text_of(response)
    rows = _index_rows(body)
    assert len(rows) == len(ARTIFACT_SPECS)
    assert body.count("Not generated yet") == len(ARTIFACT_SPECS)
    assert "tqa-artifact-missing" in body
    # No link to the artifact route at all: every artifact is absent, and that
    # route answers 404 for anything not on disk.
    artifact_prefix = _url(flask_app, "web.artifact", name=ARTIFACT_NAME_SENTINEL)
    assert ARTIFACT_NAME_SENTINEL not in body
    assert artifact_prefix.replace(ARTIFACT_NAME_SENTINEL, "") not in _hrefs(body)
    assert "No report artifact has been generated yet" in _visible_text(body)


def test_index_lists_the_four_artifacts_in_specification_order(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """Four rows, in ``ARTIFACT_SPECS`` order, each naming its own path.

    That order is the plugin order of the Java runner - the HTML report, the
    JSON report, the rerun manifest, then the report tree - and the page lists
    the rows exactly as the view hands them over, so the assertion is on the
    sequence and not merely on the set.
    """
    body = _text_of(client.get(_url(flask_app, "web.index")))
    rows = _index_rows(body)

    assert len(rows) == len(ARTIFACT_SPECS)
    for row, spec in zip(rows, ARTIFACT_SPECS, strict=True):
        assert f"<code>{spec.relpath}</code>" in row, (
            f"row for {spec.key} does not display {spec.relpath}"
        )
    # The displayed paths appear in the same order in the document, so a page
    # that rendered the right four rows in the wrong sequence fails here.
    positions = [body.index(f"<code>{spec.relpath}</code>") for spec in ARTIFACT_SPECS]
    assert positions == sorted(positions)


def test_index_links_every_servable_artifact_to_its_artifact_route(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """Each servable artifact links to ``web.artifact``, and the link works.

    The href is compared against the URL ``url_for`` builds for the name that
    artifact must be linked by - its own key for the three files, the nested
    overview page for the report tree - and then requested: a page that linked
    a name the route does not serve at 200 would render a link that 404s or
    redirects, which is worse than no link, so both halves are asserted.

    The tree also holds the worker intermediates, an unlisted file and a
    directory that is not the report tree, so "exactly one link per artifact"
    is asserted against a root carrying more than the four.
    """
    body = _text_of(client.get(_url(flask_app, "web.index")))
    hrefs = _hrefs(body)

    for spec in ARTIFACT_SPECS:
        expected = _url(
            flask_app, "web.artifact", name=_linked_artifact_name(spec)
        )
        assert hrefs.count(expected) == 1, f"{spec.key} is not linked exactly once"
        followed = client.get(expected)
        assert followed.status_code == 200, (
            f"the link the page offers for {spec.key} does not resolve"
        )
        assert followed.location is None, (
            f"the link the page offers for {spec.key} redirects rather than "
            "serving the artifact, so its relative references resolve against "
            "the wrong base URL"
        )
    assert "tqa-artifact-missing" not in body
    assert "Not generated yet" not in body


def test_index_reports_presence_both_ways_within_one_page(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """Present and absent artifacts, side by side, each rendered as itself.

    One page carrying both states is what proves the row is driven by the
    artifact rather than by the page: the present one is linked and carries a
    modification time, the absent one is marked missing, carries the words
    "Not generated yet" and is not linked at all.
    """
    present, absent = ARTIFACT_SPECS[1], ARTIFACT_SPECS[2]
    path = results_root / present.relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")

    body = _text_of(client.get(_url(flask_app, "web.index")))
    rows = {
        spec.key: row
        for spec, row in zip(ARTIFACT_SPECS, _index_rows(body), strict=True)
    }

    present_row = rows[present.key]
    assert (
        _url(flask_app, "web.artifact", name=_linked_artifact_name(present))
        in present_row
    )
    assert "tqa-artifact-missing" not in present_row
    assert "Not generated yet" not in present_row
    assert "<time datetime=" in present_row

    absent_row = rows[absent.key]
    assert "tqa-artifact-missing" in absent_row
    assert "Not generated yet" in absent_row
    assert _url(flask_app, "web.artifact", name=absent.key) not in absent_row
    assert "<time datetime=" not in absent_row


def test_index_renders_the_modification_time_it_read_from_disk(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """The rendered instant is the file's own, in the artifacts' own shape.

    The machine-readable attribute is parsed back and compared against the
    file's ``st_mtime``, so the assertion is about the instant rather than
    about a format this test would otherwise have to reimplement.  The literal
    ``Z`` is asserted separately, because that suffix is the shape the
    artifacts' own timestamps carry and the reason both forms are derived from
    one UTC datetime.  Truncation to milliseconds is the only difference the
    comparison tolerates.
    """
    spec = ARTIFACT_SPECS[1]
    path = results_root / spec.relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")

    body = _text_of(client.get(_url(flask_app, "web.index")))
    row = dict(zip((s.key for s in ARTIFACT_SPECS), _index_rows(body), strict=True))[
        spec.key
    ]

    match = re.search(r'<time datetime="([^"]+)">(.*?)</time>', row, re.DOTALL)
    assert match is not None, "the present artifact carries no time element"
    stamp, shown = match.group(1), match.group(2).strip()
    assert stamp.endswith("Z"), f"{stamp!r} is not the artifacts' UTC form"

    rendered = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    actual = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    assert 0 <= (actual - rendered).total_seconds() < 0.001, (
        f"rendered {rendered.isoformat()} is not the file's {actual.isoformat()}"
    )
    assert shown, "the human-readable form beside the attribute is empty"
    assert "UTC" in shown, "the human-readable form does not name its zone"


def test_index_never_mentions_the_worker_intermediates(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """The per-worker results are neither listed nor hinted at.

    They are created first, with distinctive content, so the absence asserted
    here is a decision the page makes and not an accident of an empty root.
    The Jenkins publisher's include pattern was narrowed to keep those files
    out of the published report; a landing page that advertised them over HTTP
    would undo that.
    """
    workers = results_root / "target" / ".workers"
    workers.mkdir(parents=True)
    (workers / "worker-1-0000.json").write_text(WORKER_PAYLOAD, encoding="utf-8")

    body = _text_of(client.get(_url(flask_app, "web.index")))

    assert ".workers" not in body
    assert WORKER_PAYLOAD not in body
    assert "worker-1-0000.json" not in body
    assert len(_index_rows(body)) == len(ARTIFACT_SPECS)


def test_index_answers_200_while_the_report_routes_answer_404(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """The landing page has no data-availability precondition, and proves it.

    One request pair, one state: with the results artifact present but
    unusable, the report route answers 404 and the landing page answers 200
    with the artifact listed as present.  That is the asymmetry AAP 0.3.1
    fixes, and it also proves the error handlers do not intercept the index.
    """
    _install_unusable_results(results_root, "unparseable")

    index = client.get(_url(flask_app, "web.index"))
    overview = client.get(_url(flask_app, "web.reports_overview"))

    assert index.status_code == 200
    assert overview.status_code == 404
    body = _text_of(index)
    assert "Not generated yet" in body  # three of the four are still absent
    json_spec = next(spec for spec in ARTIFACT_SPECS if spec.key == "cucumber.json")
    assert _url(flask_app, "web.artifact", name=json_spec.key) in _hrefs(body), (
        "an unparseable artifact is still a downloadable one"
    )


@pytest.mark.parametrize("arrangement", FOREIGN_RESULTS_ARRANGEMENTS)
def test_index_does_not_advertise_an_artifact_linked_in_from_outside(
    client: FlaskClient, flask_app: Flask, results_root: Path, arrangement: str
) -> None:
    """A symlinked or hard-linked artifact is listed as unavailable.

    The page's availability question has to be the artifact route's own, or it
    advertises what that route refuses.  The file is there, it is readable and
    it is valid; the route still answers 404 for it, so the row must carry the
    missing variant, the words the missing card uses and no link at all - and
    the row's own assertion is made, not merely the page's, so a page that
    dropped the row entirely would fail too.
    """
    _install_foreign_results(results_root, arrangement)
    json_spec = next(
        spec for spec in ARTIFACT_SPECS if spec.key == CUCUMBER_JSON_NAME
    )

    response = client.get(_url(flask_app, "web.index"))

    assert response.status_code == 200
    body = _text_of(response)
    rows = dict(
        zip((s.key for s in ARTIFACT_SPECS), _index_rows(body), strict=True)
    )
    row = rows[json_spec.key]
    assert "tqa-artifact-missing" in row
    assert "Not generated yet" in row
    assert "<time datetime=" not in row
    assert _url(flask_app, "web.artifact", name=json_spec.key) not in _hrefs(body)
    assert client.get(
        _url(flask_app, "web.artifact", name=json_spec.key)
    ).status_code == 404


def test_index_does_not_advertise_a_report_tree_without_its_overview_page(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """A half-written report tree is listed as unavailable, not linked.

    The tree is a directory, and its presence is not the question: what the
    route serves for it is the overview page inside it, so a tree whose
    generator wrote its assets but never got as far as that page is refused.
    The assets are written first, so the directory genuinely exists and this is
    the overview page being verified rather than the parent.
    """
    assets = results_root / TARGET_DIR_NAME / PRETTY_ASSET_RELPATH
    assets.parent.mkdir(parents=True)
    assets.write_text(PRETTY_ASSET_CONTENT, encoding="utf-8")
    overview = results_root / TARGET_DIR_NAME / PRETTY_OVERVIEW_RELPATH
    assert not overview.exists(), "the overview page must be the missing part"
    tree_spec = next(spec for spec in ARTIFACT_SPECS if spec.is_dir)

    response = client.get(_url(flask_app, "web.index"))

    assert response.status_code == 200
    body = _text_of(response)
    rows = dict(
        zip((s.key for s in ARTIFACT_SPECS), _index_rows(body), strict=True)
    )
    row = rows[tree_spec.key]
    assert "tqa-artifact-missing" in row
    assert "Not generated yet" in row
    assert PRETTY_OVERVIEW_RELPATH not in row
    assert not [href for href in _hrefs(body) if "/artifacts/" in href], (
        "the landing page linked an artifact route with nothing servable"
    )
    for spelling in TREE_DIRECTORY_SPELLINGS:
        assert client.get(_artifact_url(flask_app, spelling)).status_code == 404, (
            "an absent overview page must stay a 404 and not become a "
            "redirect into one"
        )


# --------------------------------------------------------------------------
# The one data-availability rule, for all four report routes: five causes, one
# response.  The states themselves are arranged by _install_unusable_results
# above, whose docstring records why "unreadable" is a directory and undecodable
# bytes rather than a mode change.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cause", UNUSABLE_RESULTS_CAUSES)
@pytest.mark.parametrize(("endpoint", "values"), REPORT_ENDPOINT_ARGS)
def test_report_route_answers_404_for_every_unusable_results_cause(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    endpoint: str,
    values: dict[str, Any],
    cause: str,
) -> None:
    """Each of the four report routes, against each of the five causes.

    Twenty cases, and none of them is a bare status assertion: the response
    must also carry the cause-neutral body its content type fixes - the
    rendered page for the three HTML routes, the two-member object for the
    summary route - so a 404 produced by a framework redirect or by a
    different template would fail.
    """
    _install_unusable_results(results_root, cause)

    response = client.get(_url(flask_app, endpoint, **values))

    assert response.status_code == 404
    if endpoint == "web.reports_summary":
        assert response.headers["Content-Type"].startswith("application/json")
        assert response.get_json() == {
            "error": response.get_json()["error"],
            "status": 404,
        }
        assert "Not found." in response.get_json()["error"]
    else:
        assert response.headers["Content-Type"].startswith("text/html")
        assert "Nothing to show here" in _text_of(response)


def test_unusable_results_answer_byte_identically_across_every_cause(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """The distinction between the causes never reaches a response.

    The assertion the rule actually makes: across all five causes and all
    three HTML report routes, there is exactly **one** body, and across the
    five causes the summary route has exactly one body of its own.  Anything
    that varied - a cause, a code, a path, an exception message - would show up
    here as a second distinct body.
    """
    html_bodies: set[bytes] = set()
    json_bodies: set[bytes] = set()

    for cause in UNUSABLE_RESULTS_CAUSES:
        _install_unusable_results(results_root, cause)
        for endpoint, values in HTML_REPORT_ENDPOINT_ARGS:
            response = client.get(_url(flask_app, endpoint, **values))
            assert response.status_code == 404
            html_bodies.add(response.data)
        summary = client.get(_url(flask_app, "web.reports_summary"))
        assert summary.status_code == 404
        json_bodies.add(summary.data)

    assert len(html_bodies) == 1, (
        f"{len(html_bodies)} distinct 404 pages across the causes and routes"
    )
    assert len(json_bodies) == 1, (
        f"{len(json_bodies)} distinct 404 bodies for the summary route"
    )
    assert WORKER_PAYLOAD not in html_bodies.pop().decode("utf-8")


def test_empty_results_list_is_a_success_not_a_404(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """``[]`` renders, at 200, with every tally at zero.

    A run whose tag expression selected no scenario still writes all four
    artifacts, so an empty list is a legitimate result and not an unusable
    one.  The feature route still answers 404 for position zero, because there
    is no feature at it - which separates "no results" from "no such feature".
    """
    _write_results(results_root, [])

    overview = client.get(_url(flask_app, "web.reports_overview"))
    summary = client.get(_url(flask_app, "web.reports_summary"))
    feature = client.get(_url(flask_app, "web.report_feature", findex=0))

    assert overview.status_code == 200
    assert summary.status_code == 200
    assert summary.get_json() == {
        "features": {"total": 0, "by_status": {}},
        "scenarios": {"total": 0, "by_status": {}},
        "steps": {"total": 0, "by_status": {}},
        "start_timestamp": None,
    }
    assert feature.status_code == 404


def test_summary_route_answers_json_whatever_the_client_asked_for(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """The JSON route's error body has the media type its success body has.

    ``app/errors.py`` decides on the failing endpoint first, so the summary
    route answers JSON even under ``Accept: text/html``, and the HTML report
    routes answer the page under the same header.  Both halves are asserted
    from one state, because it is the pair that pins the negotiation.
    """
    _install_unusable_results(results_root, "absent")
    html_only = {"Accept": "text/html"}

    summary = client.get(_url(flask_app, "web.reports_summary"), headers=html_only)
    overview = client.get(_url(flask_app, "web.reports_overview"), headers=html_only)

    assert summary.status_code == 404
    assert summary.headers["Content-Type"].startswith("application/json")
    assert summary.get_json()["status"] == 404
    assert overview.status_code == 404
    assert overview.headers["Content-Type"].startswith("text/html")


def test_html_report_route_404_negotiates_json_on_request(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """An HTML route's 404 becomes JSON only when JSON is strictly preferred.

    Three headers, three outcomes, because it is the contrast that makes the
    negotiation testable: an explicit JSON-only header yields the JSON body, a
    weighted header that prefers HTML yields the page, and ``*/*`` - which
    weighs the two equally, and is what a browser sends - yields the page too.
    """
    _install_unusable_results(results_root, "absent")
    url = _url(flask_app, "web.reports_overview")

    as_json = client.get(url, headers={"Accept": "application/json"})
    as_html = client.get(url, headers={"Accept": "application/json;q=0.8, text/html"})
    as_any = client.get(url, headers={"Accept": "*/*"})

    assert as_json.status_code == 404
    assert as_json.headers["Content-Type"].startswith("application/json")
    assert as_json.get_json()["status"] == 404
    for response in (as_html, as_any):
        assert response.status_code == 404
        assert response.headers["Content-Type"].startswith("text/html")
        assert "Nothing to show here" in _text_of(response)


# --------------------------------------------------------------------------
# Where the results a report route renders came from.  The document is read
# through the path module's verified-descriptor reader, so the routes render
# the artifact a run wrote and not whatever the results pathname happens to
# address: a symbolic link to an external file, and an entry hard-linked to
# one, are both refused, and neither the report routes nor the artifact route
# nor the landing page may disclose or advertise them.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arrangement", FOREIGN_RESULTS_ARRANGEMENTS)
@pytest.mark.parametrize(("endpoint", "values"), REPORT_ENDPOINT_ARGS)
def test_report_routes_refuse_results_linked_in_from_outside_the_root(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    endpoint: str,
    values: dict[str, Any],
    arrangement: str,
) -> None:
    """All four report routes 404 on a results file that is not the artifact.

    The external document is valid and its bytes are readable, which is
    asserted first: the refusal is therefore about where the file came from and
    not about its shape, and a route that read the pathname instead of a
    verified descriptor would render the canary at 200.  The canary is checked
    against the whole body, so an escaped or partial disclosure fails too.
    """
    external = _install_foreign_results(results_root, arrangement)
    assert isinstance(json.loads(external.read_text(encoding="utf-8")), list), (
        "the external document must be a valid feature list, or the refusal "
        "below would prove nothing about provenance"
    )

    response = client.get(_url(flask_app, endpoint, **values))

    assert response.status_code == 404
    assert EXTERNAL_RESULTS_CANARY not in _text_of(response)
    assert EXTERNAL_RESULTS_CANARY not in response.data.decode("utf-8")


@pytest.mark.parametrize("arrangement", FOREIGN_RESULTS_ARRANGEMENTS)
def test_linked_in_results_are_refused_identically_everywhere(
    client: FlaskClient, flask_app: Flask, results_root: Path, arrangement: str
) -> None:
    """One arrangement, every surface: refused, cause-neutral, unadvertised.

    The three surfaces that could leak the file are asserted together because
    it is their agreement that matters: the report route answers the canonical
    404 page, the artifact route answers the same page rather than serving the
    bytes, and the landing page - which answers 200 always - neither links the
    artifact nor mentions the external file.
    """
    external = _install_foreign_results(results_root, arrangement)
    canonical = _not_found_page(client, flask_app)

    overview = client.get(_url(flask_app, "web.reports_overview"))
    served = client.get(_url(flask_app, "web.artifact", name=CUCUMBER_JSON_NAME))
    index = client.get(_url(flask_app, "web.index"))

    _assert_rejected(
        overview,
        canonical=canonical,
        forbidden=(EXTERNAL_RESULTS_CANARY, EXTERNAL_RESULTS_NAME),
    )
    _assert_rejected(
        served,
        canonical=canonical,
        forbidden=(EXTERNAL_RESULTS_CANARY, EXTERNAL_RESULTS_NAME),
    )
    assert external.is_file(), "the external file must survive every request"
    assert index.status_code == 200
    body = _text_of(index)
    assert EXTERNAL_RESULTS_CANARY not in body
    assert EXTERNAL_RESULTS_NAME not in body
    assert _url(flask_app, "web.artifact", name=CUCUMBER_JSON_NAME) not in _hrefs(
        body
    ), "the landing page linked an artifact the route refuses"
    json_spec = next(
        spec for spec in ARTIFACT_SPECS if spec.key == CUCUMBER_JSON_NAME
    )
    rows = dict(
        zip((s.key for s in ARTIFACT_SPECS), _index_rows(body), strict=True)
    )
    assert "tqa-artifact-missing" in rows[json_spec.key]
    assert "Not generated yet" in rows[json_spec.key]


# --------------------------------------------------------------------------
# Resource budgets.  The results artifact is read and materialised on every
# request to four routes, so its size and shape are bounded; each budget's
# breach is one more cause of the same 404, and a legitimately large report
# still renders.
# --------------------------------------------------------------------------

#: The four breaches, each of which must be indistinguishable from the others
#: and from the five causes above.
BUDGET_BREACHES: Final[tuple[str, ...]] = (
    "numeric-token",
    "bytes",
    "depth",
    "collection",
)

#: The figures this suite breaches each budget by, written out rather than
#: derived from the view module, so that every test below fails on a status
#: code when the budgets are gone rather than on a missing constant.  Each is
#: chosen to be over its budget and, deliberately, within what an unguarded
#: ``json.loads`` handles without complaint: a document nested 5,000 deep would
#: exhaust the parser's own recursion and be refused for that reason instead,
#: which would prove nothing about a depth budget.  The relationship between
#: these figures and the budgets themselves is asserted separately.
OVER_BUDGET_NUMBER_DIGITS: Final[int] = 5_000
OVER_BUDGET_BYTES: Final[int] = 65 * 1024 * 1024
OVER_BUDGET_DEPTH: Final[int] = 256
OVER_BUDGET_COLLECTION_ITEMS: Final[int] = 200_000


def _budget_breaching_results(breach: str) -> bytes:
    """The bytes of a results file that breaks exactly one budget.

    Every one of them is a **usable report with one pathological member
    appended**: a real feature carrying a real scenario sits at position zero,
    so each of the four report routes has something to render and a 404 from
    any of them can only be the budget answering rather than an index that was
    out of range anyway.  The pathological member is spliced into the JSON text
    rather than built as objects, because a 256-deep structure cannot be
    serialised by a recursive encoder in the first place.

    :param breach: One of :data:`BUDGET_BREACHES`.
    :returns: The file's bytes, ready to be written at the results path.
    :raises AssertionError: If the breach is not one of the four.
    """
    usable = json.dumps(
        _results_document(
            [
                _feature(
                    "a budget test feature",
                    "features/Budget.feature",
                    [
                        _scenario(
                            "a budget test scenario",
                            steps=[_step("a step", "passed")],
                            started="2022-09-07T13:37:26.297Z",
                        )
                    ],
                )
            ]
        )
    )
    assert usable.endswith("]"), "the document must be the top-level list"

    if breach == "numeric-token":
        # A 5,000-digit integer, the reviewer's own case: past any plausible
        # numeric-token cap, and past CPython's integer-string conversion
        # limit, which is what made an unguarded parse raise ValueError and
        # answer 500 where the contract has a 404.
        extra = "9" * OVER_BUDGET_NUMBER_DIGITS
    elif breach == "bytes":
        # Over the byte budget in total while every single string stays tiny,
        # so it is the file's size that is refused and nothing else.
        chunk = json.dumps("b" * 1024)
        extra = ",".join([chunk] * ((OVER_BUDGET_BYTES // 1024) + 1))
    elif breach == "depth":
        extra = "[" * OVER_BUDGET_DEPTH + "]" * OVER_BUDGET_DEPTH
    elif breach == "collection":
        extra = json.dumps([0] * OVER_BUDGET_COLLECTION_ITEMS)
    else:
        raise AssertionError(f"unknown budget breach: {breach!r}")
    return f"{usable[:-1]},{extra}]".encode("utf-8")


def test_the_readers_budgets_sit_between_a_real_report_and_these_breaches(
) -> None:
    """Each budget is above what a run writes and below what is breached here.

    Two properties, and both have to hold for the tests around this one to mean
    anything.  Above: the per-string budget is compared with the **writer's**
    own inline-embedding ceiling, so a screenshot the report writer is entitled
    to embed can never be a document the viewer refuses to render - a
    relationship neither side can quietly break while this holds.  Below: each
    figure this suite breaches a budget by really is over it, so a passing 404
    elsewhere is the budget answering and not a coincidence.
    """
    from app.reporting.screenshots import MAX_EMBEDDING_BASE64_CHARS
    from app.web import routes as view_module

    assert view_module._MAX_STRING_CHARS >= MAX_EMBEDDING_BASE64_CHARS, (
        "the viewer would refuse a screenshot the writer is allowed to embed"
    )
    assert view_module._MAX_NUMBER_CHARS < OVER_BUDGET_NUMBER_DIGITS
    assert view_module._MAX_RESULTS_BYTES < OVER_BUDGET_BYTES
    assert view_module._MAX_NESTING_DEPTH < OVER_BUDGET_DEPTH
    assert view_module._MAX_COLLECTION_ITEMS < OVER_BUDGET_COLLECTION_ITEMS
    # And the byte budget is generous enough to hold a real report: the JSON
    # the writer produces for this suite's ten features is measured in tens of
    # kilobytes before embeddings, and one embedding at the writer's ceiling
    # has to fit beside it.
    assert view_module._MAX_RESULTS_BYTES > MAX_EMBEDDING_BASE64_CHARS


@pytest.mark.parametrize("breach", BUDGET_BREACHES)
@pytest.mark.parametrize(("endpoint", "values"), REPORT_ENDPOINT_ARGS)
def test_report_routes_answer_404_for_every_budget_breach(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    endpoint: str,
    values: dict[str, Any],
    breach: str,
) -> None:
    """A document over a budget is one more unusable-results cause, not a 500.

    Every breach must answer 404 on every report route.  The status is the
    point: a numeric token CPython refuses to convert used to escape the
    parse guard entirely and produce a sanitized 500, which told a reader the
    viewer had failed rather than that the results were unusable.
    """
    path = _results_path(results_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_budget_breaching_results(breach))

    response = client.get(_url(flask_app, endpoint, **values))

    assert response.status_code == 404, (
        f"{endpoint} answered {response.status_code} for the {breach} breach"
    )


def test_every_budget_breach_is_answered_byte_identically(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """The budgets add causes to the one rule, and no response of their own.

    One body across the four breaches and the five original causes, on the
    three HTML report routes, and one of its own on the summary route: a budget
    that reported itself - a distinct page, a message, a different code - would
    show up here as a second distinct body.
    """
    path = _results_path(results_root)
    html_bodies: set[bytes] = set()
    json_bodies: set[bytes] = set()

    for breach in BUDGET_BREACHES:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_budget_breaching_results(breach))
        for endpoint, values in HTML_REPORT_ENDPOINT_ARGS:
            response = client.get(_url(flask_app, endpoint, **values))
            assert response.status_code == 404
            html_bodies.add(response.data)
        summary = client.get(_url(flask_app, "web.reports_summary"))
        assert summary.status_code == 404
        json_bodies.add(summary.data)
    for cause in UNUSABLE_RESULTS_CAUSES:
        _install_unusable_results(results_root, cause)
        html_bodies.add(client.get(_url(flask_app, "web.reports_overview")).data)
        json_bodies.add(client.get(_url(flask_app, "web.reports_summary")).data)

    assert len(html_bodies) == 1, (
        f"{len(html_bodies)} distinct 404 pages across the breaches and causes"
    )
    assert len(json_bodies) == 1, (
        f"{len(json_bodies)} distinct 404 bodies for the summary route"
    )


def test_a_legitimately_large_report_still_renders(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """The budgets are set above a real report, not around the sample one.

    A document an order of magnitude larger than anything this suite produces -
    sixty features, each with ten scenarios of ten steps, and one embedded
    screenshot-sized base64 string - must answer 200 on the overview and be
    counted in full by the summary.  A budget tight enough to refuse this would
    refuse a real full-suite run with screenshots.
    """
    features = [
        _feature(
            f"large feature {index}",
            f"features/Large{index}.feature",
            [
                _scenario(
                    f"scenario {position}",
                    steps=[
                        _step(f"step {number}", "passed")
                        for number in range(10)
                    ],
                    started="2022-09-07T13:37:26.297Z",
                )
                for position in range(10)
            ],
        )
        for index in range(60)
    ]
    document = _results_document(features)
    # One value the size of a real inlined screenshot, to prove the per-string
    # budget is above what the writer is entitled to embed.
    document[0]["description"] = "s" * (2 * 1024 * 1024)
    path = _write_results(results_root, document)
    assert path.stat().st_size > 2 * 1024 * 1024

    overview = client.get(_url(flask_app, "web.reports_overview"))
    summary = client.get(_url(flask_app, "web.reports_summary"))

    assert overview.status_code == 200
    assert summary.status_code == 200
    assert summary.get_json()["features"]["total"] == 60
    assert summary.get_json()["scenarios"]["total"] == 600
    assert summary.get_json()["steps"]["total"] == 6_000


def _one_scenario_results() -> list[dict[str, Any]]:
    """A minimal usable results document: one feature, one scenario, one step.

    :returns: A document every one of the four report routes answers 200 for,
        which is what lets a later refusal be attributed to the thing under
        test rather than to an index that was out of range anyway.
    """
    return _results_document(
        [
            _feature(
                "a budget test feature",
                "features/Budget.feature",
                [
                    _scenario(
                        "a budget test scenario",
                        steps=[_step("a step", "passed")],
                        started="2022-09-07T13:37:26.297Z",
                    )
                ],
            )
        ]
    )


#: The three structural budgets whose real breaching figure is too large to
#: write into a unit suite - a million values, a forty-eight-megabyte string, a
#: hundred-thousand-member mapping - each paired with a figure an ordinary
#: report already exceeds.  The budget is lowered rather than the document
#: inflated because the property under test is "a document over budget B is
#: refused", which does not depend on B's production value; those values are
#: asserted separately, against the writer's own ceiling, by
#: :func:`test_the_readers_budgets_sit_between_a_real_report_and_these_breaches`.
LOWERED_BUDGETS: Final[tuple[tuple[str, int], ...]] = (
    ("_MAX_NODES", 4),
    ("_MAX_STRING_CHARS", 3),
    ("_MAX_COLLECTION_ITEMS", 2),
)


@pytest.mark.parametrize(("constant", "lowered"), LOWERED_BUDGETS)
def test_every_structural_budget_refuses_a_document_that_breaks_it(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    lowered: int,
) -> None:
    """Each structural budget, exercised on the document shape it guards.

    The byte, numeric-token, depth and list budgets are breached at their real
    figures elsewhere in this section.  The remaining three cannot be: a
    million-value document, a forty-eight-megabyte string and a
    hundred-thousand-key mapping would each make this suite cost more than the
    property is worth.  Lowering the constant under what one ordinary scenario
    already holds asserts the same thing - the walk reaches that budget,
    refuses the document there, and the refusal arrives as the one
    cause-neutral 404 rather than as a 500 - and it fails just as loudly if a
    budget is deleted or stops being enforced.  The same document is proved to
    render on all four routes first, so the 404s below are the budget's answer
    and not the shape of the input.

    :param constant: The budget attribute of the view module to lower.
    :param lowered: The value to lower it to, below what the document holds.
    """
    from app.web import routes as view_module

    canonical = _not_found_page(client, flask_app)
    _write_results(results_root, _one_scenario_results())
    for endpoint, values in REPORT_ENDPOINT_ARGS:
        assert client.get(_url(flask_app, endpoint, **values)).status_code == 200, (
            f"{endpoint} must render before {constant} is lowered, or the "
            "refusal below would prove nothing"
        )
    monkeypatch.setattr(view_module, constant, lowered)

    for endpoint, values in REPORT_ENDPOINT_ARGS:
        response = client.get(_url(flask_app, endpoint, **values))
        assert response.status_code == 404, (
            f"{endpoint} answered {response.status_code} for a document over "
            f"{constant}"
        )
        if (endpoint, values) in HTML_REPORT_ENDPOINT_ARGS:
            assert response.data == canonical, (
                f"{endpoint} rendered a page of its own for {constant}"
            )


def test_a_timestamp_that_cannot_be_read_is_reported_as_unavailable(
    results_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A descriptor that will not ``fstat`` costs a timestamp, never a page.

    The modification time beside an artifact is decoration, and the helper that
    reads it promises that no failure to read one can turn a working page into
    an error.  That promise is only visible on the failing path, which no
    request can drive - an artifact with no openable descriptor is refused by
    the call that would have produced one - so the helper is exercised
    directly, with a stream that has been closed under it.  Both halves are
    asserted: the two ``None`` values the pages render in words, and the
    WARNING record that is the only place the cause appears.

    :param results_root: The artifact root, for a real file to open and close.
    :param caplog: pytest's log capture, to prove the cause is recorded.
    """
    from app.web import routes as view_module

    path = _write_results(results_root, [])
    handle = open(path, "rb")  # noqa: SIM115 - closed deliberately below
    handle.close()

    with caplog.at_level(logging.WARNING, logger="app.web.routes"):
        modified_iso, modified_display = view_module._modification_times(
            handle, path.name
        )

    assert (modified_iso, modified_display) == (None, None)
    assert any(
        record.levelno == logging.WARNING and path.name in record.getMessage()
        for record in caplog.records
    ), "the unreadable timestamp was not recorded"


@pytest.mark.parametrize(
    ("arrangement", "level"),
    [(None, logging.DEBUG), ("symlink", logging.WARNING)],
)
def test_the_report_views_timestamp_follows_the_same_read_authority(
    results_root: Path,
    caplog: pytest.LogCaptureFixture,
    arrangement: str | None,
    level: int,
) -> None:
    """The views' artifact timestamp is refused exactly as the read is.

    Two states the report pages must survive with an em dash where the time
    would be, and which no route can reach - the absent artifact 404s before a
    view renders, and a linked-in one is refused by the read - so the helper is
    called directly.  The log level separates them, because it is the only
    thing that distinguishes the viewer's ordinary pre-run state from an object
    standing in the artifact's place: absent is DEBUG, refused is WARNING.

    :param results_root: The artifact root to arrange.
    :param caplog: pytest's log capture, for the level assertion.
    :param arrangement: ``None`` for an absent artifact, or an arrangement of
        :func:`_install_foreign_results`.
    :param level: The level the cause must be recorded at.
    """
    from app.web import routes as view_module

    if arrangement is not None:
        _install_foreign_results(results_root, arrangement)

    with caplog.at_level(logging.DEBUG, logger="app.web.routes"):
        assert view_module._artifact_modified() is None

    assert [
        record.levelno
        for record in caplog.records
        if "Modification time unavailable" in record.getMessage()
    ] == [level]


def test_a_fractional_duration_is_parsed_and_a_vast_one_is_refused(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """The numeric-token budget covers floats as well as integers.

    The JSON writer emits nanosecond integers, so a fractional number reaches
    the reader only from a document this port did not write - which is exactly
    the input the budget exists for.  Both halves are asserted, because a cap
    that refused an ordinary decimal would turn a legitimate third-party
    document into a 404: a plain fractional duration renders, and a token of
    five thousand characters is refused with the same 404 as its integer twin.
    """
    canonical = _not_found_page(client, flask_app)
    document = _one_scenario_results()
    document[0]["elements"][0]["steps"][0]["result"]["duration"] = 0.0302
    path = _write_results(results_root, document)

    assert client.get(_url(flask_app, "web.reports_overview")).status_code == 200
    assert client.get(_url(flask_app, "web.reports_summary")).status_code == 200

    usable = path.read_text(encoding="utf-8")
    assert usable.endswith("]")
    vast = f"{usable[:-1]},0.{'9' * OVER_BUDGET_NUMBER_DIGITS}]"
    path.write_text(vast, encoding="utf-8")

    response = client.get(_url(flask_app, "web.reports_overview"))

    assert response.status_code == 404
    assert response.data == canonical


# --------------------------------------------------------------------------
# GET /reports/summary - the counts, and the page they must agree with.
# --------------------------------------------------------------------------

#: The four members the summary body carries, and the whole of them.
SUMMARY_KEYS: Final[frozenset[str]] = frozenset(
    {"features", "scenarios", "steps", "start_timestamp"}
)

#: The two members of each counting block.
SUMMARY_BLOCK_KEYS: Final[frozenset[str]] = frozenset({"total", "by_status"})


def _meta_values(html: str) -> dict[str, str]:
    """The label-to-value pairs of a report page's **run-level** metadata list.

    Scoped to the first such list on purpose: the overview page carries each
    feature a second time as a block of its own, and those blocks reuse some of
    the same labels, so a scan of the whole document would answer with the last
    feature's value instead of the run's.

    :param html: A rendered report page.
    :returns: A mapping of the label text to the value markup, so a count the
        page displays can be compared with the one the summary route computes.
    :raises AssertionError: If the page carries no metadata list at all, so a
        restructured page fails here rather than yielding an empty mapping.
    """
    first = re.search(r'<dl class="tqa-meta">(.*?)</dl>', html, re.DOTALL)
    assert first is not None, "the page carries no run-level metadata list"
    return {
        label.strip(): value.strip()
        for label, value in re.findall(
            r'<dt class="tqa-meta-label">(.*?)</dt>\s*'
            r'<dd class="tqa-meta-value">(.*?)</dd>',
            first.group(1),
            re.DOTALL,
        )
    }


def _rendered_status_counts(html: str, heading: str) -> tuple[dict[str, int], int]:
    """One "by status" list of the overview page, parsed back into numbers.

    :param html: The rendered overview page.
    :param heading: The list's heading, e.g. ``Steps by status``.
    :returns: ``(by_status, total)`` - the per-status counts the page shows and
        the total from its final row, which carries no status of its own.
    :raises AssertionError: If the page carries no such list, so that a
        renamed heading fails here instead of yielding an empty comparison.
    """
    section = re.search(
        rf"<h3>{re.escape(heading)}</h3>.*?<ul class=\"tqa-summary\">(.*?)</ul>",
        html,
        re.DOTALL,
    )
    assert section is not None, f"the overview page has no {heading!r} list"

    by_status: dict[str, int] = {}
    total: int | None = None
    for status, count in re.findall(
        rf'<li class="tqa-count"(?: {STATUS_HOOK}="([a-z]+)")?>.*?'
        r"<strong>(\d+)</strong>",
        section.group(1),
        re.DOTALL,
    ):
        if status:
            by_status[status] = int(count)
        else:
            total = int(count)
    assert total is not None, f"the {heading!r} list carries no total row"
    return by_status, total


def test_summary_counts_the_sample_run_and_omits_every_zero(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
) -> None:
    """The success body: four members, three blocks, no zero count anywhere.

    The values are the ones the writer's own output determines - four
    features, nine scenarios, twenty-eight steps including the four repeated
    Background occurrences - so this is a test of the counting rules and not
    of a number typed twice.  ``by_status`` must omit a status with no
    occurrences rather than carry a zero, which is what makes the map's size
    meaningful to a client.
    """
    _write_results(results_root, sample_document)

    response = client.get(_url(flask_app, "web.reports_summary"))

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("application/json")
    body = response.get_json()
    assert set(body) == SUMMARY_KEYS
    for group in ("features", "scenarios", "steps"):
        assert set(body[group]) == SUMMARY_BLOCK_KEYS
        assert all(count > 0 for count in body[group]["by_status"].values()), (
            f"{group} carries a zero count"
        )
        assert sum(body[group]["by_status"].values()) == body[group]["total"]
    assert body["features"] == {
        "total": 4,
        "by_status": {"passed": 2, "failed": 1, "undefined": 1},
    }
    assert body["scenarios"] == {
        "total": 9,
        "by_status": {"passed": 6, "failed": 2, "undefined": 1},
    }
    assert body["steps"] == {
        "total": 28,
        "by_status": {"passed": 24, "failed": 2, "skipped": 1, "undefined": 1},
    }
    assert body["start_timestamp"] == "2022-09-07T13:37:26.297Z"


def test_summary_agrees_with_the_writers_own_aggregation(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
) -> None:
    """The route and ``build_summary`` are two readings of one calculation.

    ``app/reporting/html_report.py`` computes the same tallies for the
    generated HTML artifact, and a reader comparing the artifact with the
    viewer must never be shown two different answers.  That module's block
    carries additional flattened members for its template, so the comparison
    is on the two the route publishes - the total and the ``by_status`` map -
    plus the run's start, which both derive from the same rule.
    """
    _write_results(results_root, sample_document)
    expected = build_summary(sample_document)

    body = client.get(_url(flask_app, "web.reports_summary")).get_json()

    for group in ("features", "scenarios", "steps"):
        assert body[group]["total"] == expected[group]["total"], group
        assert body[group]["by_status"] == expected[group]["by_status"], group
    assert body["start_timestamp"] == expected["start_timestamp"]


def test_summary_agrees_with_the_counts_the_overview_page_renders(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
) -> None:
    """The JSON route and ``/reports`` cannot disagree about one run.

    The page derives its counts in Jinja and the route derives them in Python,
    from the same file, so this reads the page's own numbers back out of the
    markup and compares every one of them: the three run-level totals, the
    step tallies by status and the scenario tallies by status.
    """
    _write_results(results_root, sample_document)
    body = client.get(_url(flask_app, "web.reports_summary")).get_json()

    page = _text_of(client.get(_url(flask_app, "web.reports_overview")))
    meta = _meta_values(page)

    assert meta["Features"] == str(body["features"]["total"])
    assert meta["Scenarios"] == str(body["scenarios"]["total"])
    assert meta["Steps"] == str(body["steps"]["total"])

    step_counts, step_total = _rendered_status_counts(page, "Steps by status")
    assert step_counts == body["steps"]["by_status"]
    assert step_total == body["steps"]["total"]

    scenario_counts, scenario_total = _rendered_status_counts(
        page, "Scenarios by status"
    )
    assert scenario_counts == body["scenarios"]["by_status"]
    assert scenario_total == body["scenarios"]["total"]
    assert body["start_timestamp"] in meta["First scenario started"]


def test_summary_counts_a_background_step_and_folds_it_into_its_scenario(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """A background-only failure moves the scenario it precedes, and the feature.

    The rule that is easiest to get backwards, so it is asserted against an
    input built for it: one feature, one Background whose step fails, one
    scenario whose own step passes.  The step total must be two - a repeated
    background genuinely ran.

    The scenario is counted FAILED, because this route reads
    ``app/reporting/aggregation.py``'s effective scenario-unit status, which
    folds a Background occurrence into the scenario in front of it.  That is
    the measured Cucumber-JVM reading: for a failing Background the JVM names
    the **scenario** lines in ``rerun.txt`` and marks the scenario's own steps
    skipped, so the run's scenario tally there reports a failure.  The
    element's own reading is a separate question and stays its own - the
    scenario's ``element_status`` is still ``passed`` from its own steps, which
    is what keeps the badge on a feature page describing the element rather
    than the unit.
    """
    document = _results_document(
        [
            _feature(
                "Background failure feature",
                "features/Background.feature",
                [
                    _background(steps=[_step("the background step", "failed")]),
                    _scenario(
                        "passes on its own",
                        steps=[_step("its own step", "passed")],
                        started="2022-09-07T14:00:00.000Z",
                    ),
                ],
            )
        ]
    )
    _write_results(results_root, document)

    body = client.get(_url(flask_app, "web.reports_summary")).get_json()

    assert body["steps"] == {"total": 2, "by_status": {"passed": 1, "failed": 1}}
    assert body["scenarios"] == {"total": 1, "by_status": {"failed": 1}}
    assert body["features"] == {"total": 1, "by_status": {"failed": 1}}


def test_summary_start_timestamp_is_the_earliest_not_the_first(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """The run's start is the earliest scenario start, wherever it sits.

    Built so the two answers differ: the feature's first scenario started an
    hour after the one below it, so a route that reported the first value it
    found would answer the later timestamp.  The background carries none at
    all, which is why only scenario elements are considered.
    """
    document = _results_document(
        [
            _feature(
                "Out of order feature",
                "features/Order.feature",
                [
                    _background(steps=[_step("setup", "passed")]),
                    _scenario(
                        "the late one",
                        steps=[_step("a step", "passed")],
                        line=5,
                        started="2022-09-07T15:00:00.000Z",
                    ),
                    _scenario(
                        "the early one",
                        steps=[_step("a step", "passed")],
                        line=9,
                        started="2022-09-07T09:00:00.000Z",
                    ),
                ],
            )
        ]
    )
    _write_results(results_root, document)
    starts = [
        element["start_timestamp"]
        for element in document[0]["elements"]
        if element["type"] == "scenario"
    ]
    assert starts == ["2022-09-07T15:00:00.000Z", "2022-09-07T09:00:00.000Z"], (
        "the input no longer puts the later scenario first"
    )

    body = client.get(_url(flask_app, "web.reports_summary")).get_json()

    assert body["start_timestamp"] == "2022-09-07T09:00:00.000Z"


# --------------------------------------------------------------------------
# Positional keys.  Both indices are positions, and the two id collisions are
# why: keying on an id would serve one member of a pair for the other.
# --------------------------------------------------------------------------

#: Substituted into a ``url_for``-built report URL to send an index that is not
#: an integer at all.  A value no test data can contain, so the substitution
#: cannot hit anything else in the path.
INDEX_SENTINEL: Final[int] = 987654321


def _feature_url_with(flask_app: Flask, raw_index: str) -> str:
    """A feature URL carrying ``raw_index`` verbatim in the index position.

    :param flask_app: The application whose URL map answers.
    :param raw_index: The text to place where the converter expects an integer.
    :returns: The path to request.
    """
    template = _url(flask_app, "web.report_feature", findex=INDEX_SENTINEL)
    return template.replace(str(INDEX_SENTINEL), raw_index)


def _scenario_url_with(flask_app: Flask, raw_index: str) -> str:
    """A scenario URL carrying ``raw_index`` verbatim in the scenario position.

    :param flask_app: The application whose URL map answers.
    :param raw_index: The text to place where the converter expects an integer.
    :returns: The path to request.
    """
    template = _url(
        flask_app, "web.report_scenario", findex=0, sindex=INDEX_SENTINEL
    )
    return template.replace(str(INDEX_SENTINEL), raw_index)


def test_features_sharing_an_id_are_reachable_at_their_own_indices(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
) -> None:
    """The Contact/Inventory collision: one id, two features, two pages.

    Both are titled *Testinium app Inventory feature*, so the writer derives
    the same id for both - the check below proves that rather than assuming
    it.  Each must be served at its own position, distinguished by the one
    thing that does differ, its ``uri``; and the shared id must still be
    rendered as the data it is, undisambiguated, because the collision is
    source behaviour this port preserves.
    """
    pair = [
        (index, feature)
        for index, feature in enumerate(sample_document)
        if feature["id"] == "testinium-app-inventory-feature"
    ]
    assert len(pair) == 2, "the sample run no longer carries the collision pair"
    assert pair[0][1]["uri"] != pair[1][1]["uri"]
    _write_results(results_root, sample_document)

    for index, feature in pair:
        page = _text_of(
            client.get(_url(flask_app, "web.report_feature", findex=index))
        )
        assert feature["uri"] in page, f"index {index} served the wrong feature"
        other = next(other for position, other in pair if position != index)
        assert other["uri"] not in page
        # The id is displayed, exactly as the artifact carries it: no suffix,
        # no counter, no rewrite.
        assert feature["id"] in page
        assert f"{feature['id']}-1" not in page
        assert f"{feature['id']}-2" not in page


def test_the_second_title_collision_is_keyed_by_position_too(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """The Login/Notes collision, built through the writer that derives the id.

    ``Login.feature`` and ``Notes.feature`` are both titled *Testinium app
    login feature*, and neither the sample run nor the golden baseline carries
    the pair, so it is built here from the internal schema and run through the
    production writer - which derives the id from the name with the JVM's own
    slug rule, so the collision is reproduced rather than asserted against a
    hand-typed slug.
    """
    document = _results_document(
        [
            _feature(
                "Testinium app login feature",
                "features/Login.feature",
                [
                    _scenario(
                        "the user can log in",
                        steps=[_step("a login step", "passed")],
                        started="2022-09-07T13:00:00.000Z",
                    )
                ],
            ),
            _feature(
                "Testinium app login feature",
                "features/Notes.feature",
                [
                    _scenario(
                        "the user can write a note",
                        steps=[_step("a note step", "passed")],
                        started="2022-09-07T13:05:00.000Z",
                    )
                ],
            ),
        ]
    )
    assert document[0]["id"] == document[1]["id"], "the writer disambiguated the id"
    _write_results(results_root, document)

    first = _text_of(client.get(_url(flask_app, "web.report_feature", findex=0)))
    second = _text_of(client.get(_url(flask_app, "web.report_feature", findex=1)))

    assert "the user can log in" in first
    assert "the user can write a note" not in first
    assert "the user can write a note" in second
    assert "the user can log in" not in second
    assert document[0]["id"] in first
    assert document[1]["id"] in second


@pytest.mark.parametrize("offset", [0, 1, 4096])
def test_feature_index_past_the_end_answers_404(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
    offset: int,
) -> None:
    """The boundary itself, one past it, and far beyond it.

    ``len(features)`` is the first invalid position - zero-based indices stop
    one short of it - so it is the case a fence-post error breaks, and it is
    tested alongside the last valid position in the same state, which must
    still answer 200.
    """
    _write_results(results_root, sample_document)
    count = len(sample_document)

    last_valid = client.get(
        _url(flask_app, "web.report_feature", findex=count - 1)
    )
    beyond = client.get(_url(flask_app, "web.report_feature", findex=count + offset))

    assert last_valid.status_code == 200
    assert beyond.status_code == 404
    assert "Nothing to show here" in _text_of(beyond)


def test_negative_feature_index_answers_404(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
) -> None:
    """A negative position is refused by the router, not indexed backwards.

    Python would happily read ``features[-1]``, which is why this matters: a
    negative segment must never serve the last feature.  The body proves it is
    the not-found page and the comparison proves it is not the last feature's.
    """
    _write_results(results_root, sample_document)

    response = client.get(_url(flask_app, "web.report_feature", findex=-1))

    assert response.status_code == 404
    body = _text_of(response)
    assert "Nothing to show here" in body
    assert sample_document[-1]["uri"] not in body


@pytest.mark.parametrize("raw_index", ["abc", "1.0", "0x0", "", "%20", "1%2C2"])
def test_non_integer_feature_index_answers_404(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
    raw_index: str,
) -> None:
    """Anything the integer converter does not accept is a 404.

    Six representations, including the empty segment and an encoded one, all
    answered by the router before the view is reached - which is the point:
    the view never has to defend against a non-integer index because the rule
    cannot match one.
    """
    _write_results(results_root, sample_document)

    response = client.get(_feature_url_with(flask_app, raw_index))

    assert response.status_code == 404
    assert sample_document[0]["uri"] not in _text_of(response)


def test_scenario_index_counts_scenario_elements_only(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
) -> None:
    """The interleaved backgrounds consume no index, and nothing is off by one.

    The sample run's second feature is the shape that makes this testable: its
    elements interleave Background, scenario, Background, scenario, Background,
    scenario, Background, scenario.  So the four scenario positions must serve
    the four scenarios in file order, position four must be a 404, and no
    position may serve a background as though it were a scenario.
    """
    findex = next(
        index
        for index, feature in enumerate(sample_document)
        if any(
            element["type"] == "background" for element in feature["elements"]
        )
    )
    elements = sample_document[findex]["elements"]
    scenarios = [
        element for element in elements if element["type"] == "scenario"
    ]
    assert len(scenarios) == 4, "the sample feature no longer carries four scenarios"
    _write_results(results_root, sample_document)

    for sindex, scenario in enumerate(scenarios):
        response = client.get(
            _url(flask_app, "web.report_scenario", findex=findex, sindex=sindex)
        )
        assert response.status_code == 200
        body = _text_of(response)
        assert scenario["id"] in body, (
            f"scenario position {sindex} served something else"
        )

    past_the_end = client.get(
        _url(
            flask_app, "web.report_scenario", findex=findex, sindex=len(scenarios)
        )
    )
    assert past_the_end.status_code == 404


def test_scenario_page_carries_the_background_that_preceded_it(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
) -> None:
    """The background occurrence belonging to that scenario, and its steps.

    Each repeated copy belongs to the scenario it preceded, so the page must
    show the one immediately before this scenario - rendered as a background
    rather than as another scenario, which is what the dedicated class marks.
    """
    findex = 1
    elements = sample_document[findex]["elements"]
    assert elements[0]["type"] == "background"
    background = elements[0]
    _write_results(results_root, sample_document)

    body = _text_of(
        client.get(
            _url(flask_app, "web.report_scenario", findex=findex, sindex=0)
        )
    )

    assert "tqa-background" in body, "the background is not rendered as one"
    assert _escaped(background["name"]) in body
    expected = [
        (
            step["result"]["status"],
            step["keyword"].strip(),
            _escaped(step["name"]),
        )
        for step in background["steps"]
    ]
    assert _holds_run_of(_step_rows(body), expected), (
        "the background's own steps are not rendered with it"
    )


def test_both_indices_out_of_range_answers_one_404(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
) -> None:
    """A bad feature position with a bad scenario position is still one 404.

    Three shapes in one state - feature valid and scenario past the end,
    feature past the end with scenario zero, and both past the end - because
    the route resolves them in that order and a reader must not be able to
    tell which of the two indices was refused.
    """
    _write_results(results_root, sample_document)
    count = len(sample_document)

    responses = [
        client.get(
            _url(flask_app, "web.report_scenario", findex=0, sindex=4096)
        ),
        client.get(
            _url(flask_app, "web.report_scenario", findex=count, sindex=0)
        ),
        client.get(
            _url(flask_app, "web.report_scenario", findex=count, sindex=4096)
        ),
    ]

    assert [response.status_code for response in responses] == [404, 404, 404]
    assert len({response.data for response in responses}) == 1, (
        "the response tells the caller which index was refused"
    )


@pytest.mark.parametrize("raw_index", ["abc", "-1", "1.5"])
def test_non_integer_scenario_index_answers_404(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
    raw_index: str,
) -> None:
    """The scenario position is governed by the same converter as the first.

    Asserted separately from the feature index because the rule carries two
    converters and a regression could reach only the second.
    """
    _write_results(results_root, sample_document)

    response = client.get(_scenario_url_with(flask_app, raw_index))

    assert response.status_code == 404
    assert "Nothing to show here" in _text_of(response)


# --------------------------------------------------------------------------
# What the three HTML report views actually render.
# --------------------------------------------------------------------------


def test_overview_lists_every_feature_and_links_it_by_position(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
) -> None:
    """Every feature's name, and one link per feature, keyed by position.

    The link targets are compared against the URLs ``url_for`` builds for the
    positions, which is the assertion that the page keys by index: a page that
    linked by id would offer the same URL twice for the collision pair.
    """
    _write_results(results_root, sample_document)

    body = _text_of(client.get(_url(flask_app, "web.reports_overview")))
    hrefs = _hrefs(body)

    for index, feature in enumerate(sample_document):
        assert _escaped(feature["name"]) in body, (
            f"feature {index} is not named on the page"
        )
        expected = _url(flask_app, "web.report_feature", findex=index)
        assert expected in hrefs, f"feature {index} is not linked"
    # One distinct feature URL per feature, so no two features share a link.
    feature_links = {
        href for href in hrefs if re.fullmatch(r"/reports/features/\d+", href)
    }
    assert len(feature_links) == len(sample_document)


def test_feature_page_lists_its_own_scenarios_and_links_them_by_position(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
) -> None:
    """That feature's scenarios, its own alone, each linked by its position.

    A second feature's scenario name must not appear, which is what makes this
    a test of the lookup and not merely of the template: the sample run's
    features carry distinct scenario names, so a page that rendered the whole
    document would fail here.
    """
    findex = 1
    feature = sample_document[findex]
    scenarios = [
        element for element in feature["elements"] if element["type"] == "scenario"
    ]
    _write_results(results_root, sample_document)

    body = _text_of(
        client.get(_url(flask_app, "web.report_feature", findex=findex))
    )
    hrefs = _hrefs(body)

    assert _escaped(feature["name"]) in body
    for sindex, scenario in enumerate(scenarios):
        assert _escaped(scenario["name"]) in body
        assert (
            _url(
                flask_app, "web.report_scenario", findex=findex, sindex=sindex
            )
            in hrefs
        )
    foreign = sample_document[0]["elements"][0]["name"]
    assert _escaped(foreign) not in body, (
        "the page rendered another feature's scenario"
    )


def test_scenario_page_renders_every_step_with_its_status(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
) -> None:
    """Each step's keyword, name and status, as one row, in file order.

    The three facts are asserted together, from the row the shared step
    partial emits, and as a contiguous block: a page that rendered the right
    step names with the wrong statuses, or the right statuses against the
    wrong steps, or the steps in the wrong order, fails here.  The scenario
    under test carries a passed, a failed and a skipped step, so all three
    status classes are exercised in one request.
    """
    findex, sindex = 1, 1
    feature = sample_document[findex]
    scenario = [
        element for element in feature["elements"] if element["type"] == "scenario"
    ][sindex]
    statuses = {step["result"]["status"] for step in scenario["steps"]}
    assert statuses == {"passed", "failed", "skipped"}, (
        "the sample scenario no longer carries three distinct statuses"
    )
    _write_results(results_root, sample_document)

    body = _text_of(
        client.get(
            _url(
                flask_app, "web.report_scenario", findex=findex, sindex=sindex
            )
        )
    )

    assert _escaped(scenario["name"]) in body
    expected = [
        (
            step["result"]["status"],
            step["keyword"].strip(),
            _escaped(step["name"]),
        )
        for step in scenario["steps"]
    ]
    rows = _step_rows(body)
    assert _holds_run_of(rows, expected), (
        f"the scenario's steps are not rendered as {expected}; page has {rows}"
    )
    # The shared partials are what render a step row and its badge.
    assert "tqa-badge" in body


def test_failed_scenario_embeds_its_screenshot_as_a_data_uri(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    sample_document: list[dict[str, Any]],
) -> None:
    """A failure's screenshot is rendered inline, from the artifact's payload.

    The embedding lives on the scenario's teardown hook, and the page must
    render it as a ``data:`` URI carrying the artifact's own base64 payload -
    not a link to a file on disk, which would be a path the viewer does not
    serve.  The lightbox partial that presents it is asserted alongside.
    """
    findex, sindex = 1, 1
    scenario = [
        element
        for element in sample_document[findex]["elements"]
        if element["type"] == "scenario"
    ][sindex]
    embedding = scenario["after"][0]["embeddings"][0]
    assert embedding["mime_type"] == "image/png"
    _write_results(results_root, sample_document)

    body = _text_of(
        client.get(
            _url(
                flask_app, "web.report_scenario", findex=findex, sindex=sindex
            )
        )
    )

    assert f"data:{embedding['mime_type']};base64,{embedding['data']}" in body
    assert "tqa-shot" in body
    assert "tqa-lightbox" in body
    assert 'src="/' not in body.split("tqa-shot")[1][:400], (
        "the screenshot is served from a path rather than embedded"
    )


def test_non_ascii_step_text_survives_to_the_rendered_page(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """The French validation message reaches the page as the characters it is.

    ``features/Login.feature:89`` asserts that exact string, so it is the
    realistic non-ASCII payload for this path: it goes through the JSON writer,
    onto disk as UTF-8 characters rather than escapes, back through the view
    and into the response, and the response bytes are decoded explicitly here
    so that the proof is about the bytes on the wire.
    """
    document = _results_document(
        [
            _feature(
                "Testinium app login feature",
                "features/Login.feature",
                [
                    _scenario(
                        "the browser reports the empty field",
                        steps=[
                            _step(
                                f'User sees the message "{FRENCH_VALIDATION_MESSAGE}"',
                                "passed",
                            )
                        ],
                        started="2022-09-07T13:00:00.000Z",
                    )
                ],
            )
        ]
    )
    _write_results(results_root, document)
    assert FRENCH_VALIDATION_MESSAGE in _results_path(results_root).read_text(
        encoding="utf-8"
    ), "the writer did not put the characters in the file"

    response = client.get(
        _url(flask_app, "web.report_scenario", findex=0, sindex=0)
    )

    assert response.status_code == 200
    assert "charset=utf-8" in response.headers["Content-Type"].lower()
    assert FRENCH_VALIDATION_MESSAGE in _text_of(response)
    assert FRENCH_VALIDATION_MESSAGE.encode("utf-8") in response.data


# --------------------------------------------------------------------------
# A parseable but malformed document.  The list is what the availability rule
# checks; everything inside it is data a reader turns to when a run has gone
# wrong, so a malformed member answers a neutral value rather than taking the
# page down.  These are 200s by requirement, not by tolerance.
# --------------------------------------------------------------------------

#: A document that satisfies the one structural rule - a list - and violates
#: every expectation inside it: a feature that is not a mapping, an element
#: list that is not a list, an element that is not a mapping, an element with
#: no type, a step list that is not a list, a step that is not a mapping, a
#: result that is not a mapping, and four statuses the model never produces.
MALFORMED_DOCUMENT: Final[list[Any]] = [
    "this feature is a string",
    {"name": "elements is a string", "elements": "not a list"},
    {
        "name": "hostile members",
        "elements": [
            {
                "type": "scenario",
                "name": "steps is a string",
                "steps": "nope",
                "start_timestamp": 12345,
            },
            {"type": None, "name": "no type at all", "steps": []},
            42,
            {
                # Odd casing and a trailing space: the templates normalise the
                # type with trim and lower, and this module's twin of that rule
                # has to agree, or the element would be counted as neither a
                # scenario nor a background.
                "type": "Scenario ",
                "name": "odd casing",
                "steps": [
                    "a step that is a string",
                    {
                        "keyword": "Given ",
                        "name": "result is a string",
                        "result": "nope",
                    },
                    {
                        "keyword": "When ",
                        "name": "status is a number",
                        "result": {"status": 7},
                    },
                    {
                        "keyword": "Then ",
                        "name": "status is none",
                        "result": {"status": None},
                    },
                    {
                        "keyword": "And ",
                        "name": "status is unheard of",
                        "result": {"status": "weird"},
                    },
                ],
                "start_timestamp": "not-a-timestamp",
            },
        ],
    },
]


def test_report_views_render_a_malformed_document_without_failing(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """Every malformed member renders; none of them is a 500.

    The document is a list, so the availability rule admits it, and every
    lookup below that level is total by requirement.  The three views and the
    two detail positions that exist are all requested, and the one position
    that does not exist is requested too, so the boundary is still a 404 rather
    than being swallowed by the tolerance.
    """
    _write_results(results_root, MALFORMED_DOCUMENT)

    statuses = {
        url: client.get(url).status_code
        for url in (
            _url(flask_app, "web.reports_overview"),
            _url(flask_app, "web.report_feature", findex=0),
            _url(flask_app, "web.report_feature", findex=1),
            _url(flask_app, "web.report_feature", findex=2),
            _url(flask_app, "web.report_scenario", findex=2, sindex=0),
            _url(flask_app, "web.report_scenario", findex=2, sindex=1),
            _url(flask_app, "web.reports_summary"),
        )
    }

    assert all(status == 200 for status in statuses.values()), statuses
    assert (
        client.get(
            _url(flask_app, "web.report_scenario", findex=2, sindex=2)
        ).status_code
        == 404
    )


def test_summary_folds_every_unrecognised_status_to_unknown(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """The route's answer for a malformed document IS the authority's answer.

    The rules belong to ``app/reporting/aggregation.py``, so the comparison is
    against that module reading the same document rather than against tokens
    typed here: this test pins the contract that moved - viewer equals
    authority - and stays correct when the authority refines a fold, which is
    the whole reason the viewer no longer keeps a copy of one.  A hard-coded
    element-level token would instead pin today's fold and fail the next time
    the empty-element or hook-inclusive rule is stated more precisely.

    The structural facts are asserted alongside, because they hold whatever
    the fold says: three features (the string member is a feature-shaped
    position and keeps it), two scenarios (the element with no type at all is
    counted as neither), four steps (the member that is a string is not a step
    and is dropped rather than counted as unknown), every ``by_status`` map
    summing to its own total, no zero count anywhere, and - the substance of
    "a status the model never produced is never a pass" - nothing counted as
    ``passed`` at step level, where all four statuses are unrecognised.
    """
    from app.reporting.aggregation import (
        SUMMARY_BY_STATUS_KEY,
        SUMMARY_GROUPS,
        SUMMARY_START_KEY,
        SUMMARY_TOTAL_KEY,
        as_mapping,
        build_summary,
    )

    _write_results(results_root, MALFORMED_DOCUMENT)
    authority = build_summary([as_mapping(member) for member in MALFORMED_DOCUMENT])

    body = client.get(_url(flask_app, "web.reports_summary")).get_json()

    assert set(body) == SUMMARY_KEYS
    for group in SUMMARY_GROUPS:
        assert set(body[group]) == SUMMARY_BLOCK_KEYS, group
        expected = authority[group]
        assert body[group][SUMMARY_TOTAL_KEY] == expected[SUMMARY_TOTAL_KEY], group
        assert (
            body[group][SUMMARY_BY_STATUS_KEY] == expected[SUMMARY_BY_STATUS_KEY]
        ), group
    assert body[SUMMARY_START_KEY] == authority[SUMMARY_START_KEY]
    assert body[SUMMARY_START_KEY] is None, (
        "the document carries a number and an unparseable string, and neither "
        "is a run start"
    )

    assert body["features"]["total"] == 3
    assert body["scenarios"]["total"] == 2
    assert body["steps"]["total"] == 4
    for group in SUMMARY_GROUPS:
        counts = body[group][SUMMARY_BY_STATUS_KEY]
        assert all(count > 0 for count in counts.values()), f"{group} carries a zero"
        assert sum(counts.values()) == body[group][SUMMARY_TOTAL_KEY], group
    assert "passed" not in body["steps"][SUMMARY_BY_STATUS_KEY], (
        "a status the result model never produced was reported as a pass"
    )


def test_summary_drops_a_start_timestamp_it_cannot_use(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """An unusable timestamp is dropped, never reported as the run's start.

    Four scenarios carry a number, an unparseable string, a blank string and
    one real value, and a background carries an earlier value that must be
    ignored because a background is not a scenario.  The answer is the one
    usable scenario value; with that scenario removed, the answer is null
    rather than a malformed string.
    """
    scenarios: list[dict[str, Any]] = [
        {"type": "scenario", "name": "a number", "steps": [], "start_timestamp": 12345},
        {
            "type": "scenario",
            "name": "unparseable",
            "steps": [],
            "start_timestamp": "not-a-timestamp",
        },
        {"type": "scenario", "name": "blank", "steps": [], "start_timestamp": "   "},
        {
            "type": "background",
            "name": "a background, which has no start of its own",
            "steps": [],
            "start_timestamp": "1999-01-01T00:00:00.000Z",
        },
    ]
    usable = {
        "type": "scenario",
        "name": "usable",
        "steps": [],
        "start_timestamp": "2022-09-07T10:00:00.000Z",
    }

    _write_results(
        results_root, [{"name": "timestamps", "elements": [*scenarios, usable]}]
    )
    with_usable = client.get(_url(flask_app, "web.reports_summary")).get_json()

    _write_results(results_root, [{"name": "timestamps", "elements": scenarios}])
    without_usable = client.get(_url(flask_app, "web.reports_summary")).get_json()

    assert with_usable["start_timestamp"] == "2022-09-07T10:00:00.000Z"
    assert without_usable["start_timestamp"] is None


# --------------------------------------------------------------------------
# One normalized result model, across every surface.
#
# AAP 0.4.2's invariant list requires it - "Both HTML artifacts and the HTTP
# views render over one normalized result model ... so no view contradicts an
# artifact" - and these are the two readings that used to disagree, plus the
# malformed shapes that used to make the page and the summary route answer
# differently.  Each test therefore asserts the SAME fact on every surface
# that states it, which is what a reader comparing two of them would do.
# --------------------------------------------------------------------------

#: The status hooks a page may carry for an element with nothing in it.  The
#: authority folds such an element to ``passed`` - measured from the reference
#: generator's rendering of the step-less Background in ``EmployeeFc.feature``
#: - and ``unknown`` is kept for a status the model never produced, so
#: ``unknown`` appearing on one of these pages is the defect itself.
EMPTY_ELEMENT_HOOK: Final[str] = "passed"


def _status_hooks(html: str) -> list[str]:
    """Every status hook value the page's own region carries, in order.

    Scoped to ``<main>`` because the shell emits a hook of its own on that
    element, and read as one list because the question these tests ask is
    "does any surface on this page grade this element differently".

    :param html: A rendered report page.
    :returns: One token per occurrence of :data:`STATUS_HOOK`, in document
        order, badges and filterable sections alike.
    """
    return re.findall(rf'{STATUS_HOOK}="([a-z]+)"', _main_region(html))


def _overview_row_statuses(html: str) -> list[str]:
    """The status of every statistics-table row, in page order.

    :param html: The rendered overview page.
    :returns: One token per feature row.
    :raises AssertionError: If the page carries no table body, so a
        restructured table fails here rather than yielding an empty list.
    """
    body = re.search(r"<tbody>(.*?)</tbody>", html, re.DOTALL)
    assert body is not None, "the overview page has no statistics table body"
    return re.findall(
        rf'<tr data-report-filterable {STATUS_HOOK}="([a-z]+)">', body.group(1)
    )


def _section_status(html: str, element_id: str) -> str:
    """The status of one page section, addressed by its own DOM id.

    :param html: A rendered feature page.
    :param element_id: The section's id, which the page derives from the two
        positional keys and never from an identifier slug.
    :returns: That section's status token.
    :raises AssertionError: If no such section carries a status, so a renamed
        id or a dropped hook fails here.
    """
    match = re.search(
        rf'id="{re.escape(element_id)}" {STATUS_HOOK}="([a-z]+)"', html
    )
    assert match is not None, (
        f"no section with id {element_id!r} carries a status hook"
    )
    return match.group(1)


def _scenario_page_status(html: str) -> str:
    """The status the scenario page badges its scenario with.

    :param html: The rendered scenario page.
    :returns: The status token on the page's scenario section.
    :raises AssertionError: If that section carries none.
    """
    match = re.search(
        rf'<section class="tqa-scenario" {STATUS_HOOK}="([a-z]+)"', html
    )
    assert match is not None, "the scenario page's section carries no status"
    return match.group(1)


def _feature_with_failed_teardown() -> list[dict[str, Any]]:
    """One feature whose scenario passed every step and failed its teardown.

    Written directly rather than through the JSON writer, and the reason is
    the writer's own documented rule: it emits an ``after`` entry only for a
    hook that produced an attachment, so a failed teardown whose capture was
    suppressed leaves no entry to grade.  A document from the reference
    producer carries the hook either way, and how the viewer grades one is
    exactly the defect under test - so the shape is stated here, as the
    Cucumber-JVM schema defines it.

    :returns: A one-feature document: a Background that passed, and a scenario
        whose single step passed while its after-hook failed.
    """
    return [
        {
            "uri": "file:features/Teardown.feature",
            "keyword": "Feature",
            "line": 1,
            "name": "Teardown failure feature",
            "description": "",
            "tags": [],
            "elements": [
                {
                    "type": "background",
                    "keyword": "Background",
                    "line": 3,
                    "name": "shared setup",
                    "description": "",
                    "steps": [_step("the background step", "passed")],
                },
                {
                    "type": "scenario",
                    "keyword": "Scenario",
                    "line": 5,
                    "id": "teardown-failure-feature;passes-until-its-teardown",
                    "name": "passes until its teardown",
                    "description": "",
                    "start_timestamp": "2022-09-07T13:00:00.000Z",
                    "tags": [],
                    "steps": [_step("its own step", "passed")],
                    "after": [
                        {
                            "match": {
                                "location": "features.environment.after_scenario"
                            },
                            "result": {"status": "failed", "duration": 5_000_000},
                        }
                    ],
                },
            ],
        }
    ]


def test_a_failed_after_hook_is_reported_by_every_surface(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """A failed teardown is a failed scenario on all four surfaces.

    This is the divergence the normalized model exists to remove: the viewer
    derived a scenario's status from its steps alone, so a scenario whose
    steps all passed but whose after-hook failed read *passed* at
    ``/reports/summary``, in the overview row, on the feature page's element
    badge and on the scenario page's badge, while both generated HTML
    artifacts read *failed* for the same element.

    The expected token is taken from ``aggregation.element_status``, the one
    place that fold is implemented, so this asserts agreement with the
    authority rather than agreement with a word typed here.  The step tally is
    asserted alongside, because a hook must move the status without becoming a
    step: two steps, both passed.
    """
    from app.reporting.aggregation import element_status

    document = _feature_with_failed_teardown()
    scenario = document[0]["elements"][1]
    expected = element_status(scenario)
    assert expected == "failed", (
        "the authority no longer grades a failed after-hook as a failure; "
        "this input no longer exercises the divergence"
    )
    _write_results(results_root, document)

    summary = client.get(_url(flask_app, "web.reports_summary")).get_json()
    overview = _text_of(client.get(_url(flask_app, "web.reports_overview")))
    feature_page = _text_of(
        client.get(_url(flask_app, "web.report_feature", findex=0))
    )
    scenario_page = _text_of(
        client.get(_url(flask_app, "web.report_scenario", findex=0, sindex=0))
    )

    assert summary["scenarios"] == {"total": 1, "by_status": {expected: 1}}
    assert summary["features"] == {"total": 1, "by_status": {expected: 1}}
    assert summary["steps"] == {"total": 2, "by_status": {"passed": 2}}, (
        "a hook is not a step"
    )
    assert _overview_row_statuses(overview) == [expected]
    assert _section_status(feature_page, "tqa-scenario-0-0") == expected
    assert _scenario_page_status(scenario_page) == expected
    # And the step rows still say what they are: the scenario failed, its
    # steps did not.
    assert [status for status, _keyword, _name in _step_rows(scenario_page)] == [
        "passed",
        "passed",
    ]


def test_an_element_with_nothing_in_it_is_passed_on_every_surface(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """An element with neither steps nor hooks is passed, never unknown.

    The second half of the same divergence, in the other direction: the viewer
    folded an empty element to ``unknown`` while the writers fold it to
    ``passed``.  That is not a preference - ``EmployeeFc.feature`` declares a
    Background with an empty body, 5.6.1's ``StatusCounter`` answers
    ``PASSED`` for an empty counter, and the reference generator renders those
    occurrences as passed - so ``unknown`` must not appear as a status
    anywhere on these pages.
    """
    from app.reporting.aggregation import element_status

    document = [
        {
            "uri": "file:features/Empty.feature",
            "keyword": "Feature",
            "line": 1,
            "name": "Empty element feature",
            "description": "",
            "tags": [],
            "elements": [
                {
                    "type": "background",
                    "keyword": "Background",
                    "line": 3,
                    "name": "a background with an empty body",
                    "description": "",
                    "steps": [],
                },
                {
                    "type": "scenario",
                    "keyword": "Scenario",
                    "line": 5,
                    "id": "empty-element-feature;nothing-at-all",
                    "name": "nothing at all",
                    "description": "",
                    "start_timestamp": "2022-09-07T13:00:00.000Z",
                    "tags": [],
                    "steps": [],
                },
            ],
        }
    ]
    for element in document[0]["elements"]:
        assert element_status(element) == EMPTY_ELEMENT_HOOK, (
            "the authority no longer folds an empty element to passed"
        )
    _write_results(results_root, document)

    summary = client.get(_url(flask_app, "web.reports_summary")).get_json()
    overview = _text_of(client.get(_url(flask_app, "web.reports_overview")))
    feature_page = _text_of(
        client.get(_url(flask_app, "web.report_feature", findex=0))
    )
    scenario_page = _text_of(
        client.get(_url(flask_app, "web.report_scenario", findex=0, sindex=0))
    )

    assert summary["scenarios"] == {
        "total": 1,
        "by_status": {EMPTY_ELEMENT_HOOK: 1},
    }
    assert summary["features"] == {
        "total": 1,
        "by_status": {EMPTY_ELEMENT_HOOK: 1},
    }
    assert summary["steps"] == {"total": 0, "by_status": {}}
    assert _overview_row_statuses(overview) == [EMPTY_ELEMENT_HOOK]
    assert _section_status(feature_page, "tqa-scenario-0-0") == EMPTY_ELEMENT_HOOK
    assert _scenario_page_status(scenario_page) == EMPTY_ELEMENT_HOOK
    for page, name in (
        (overview, "the overview"),
        (feature_page, "the feature page"),
        (scenario_page, "the scenario page"),
    ):
        assert set(_status_hooks(page)) <= {EMPTY_ELEMENT_HOOK}, (
            f"{name} grades an empty element as something other than passed: "
            f"{sorted(set(_status_hooks(page)))}"
        )


def _summary_totals(client: FlaskClient, flask_app: Flask) -> dict[str, int]:
    """The three totals ``GET /reports/summary`` reports.

    :param client: A client bound to the application under test.
    :param flask_app: That application, for URL generation.
    :returns: A mapping of group name to its total.
    """
    body = client.get(_url(flask_app, "web.reports_summary")).get_json()
    return {group: body[group]["total"] for group in ("features", "scenarios", "steps")}


def _overview_totals(html: str) -> dict[str, int]:
    """The three totals the overview page displays, read back out of it.

    :param html: The rendered overview page.
    :returns: A mapping with the same keys :func:`_summary_totals` answers
        with, so the two can be compared directly.
    """
    meta = _meta_values(html)
    return {
        "features": int(meta["Features"]),
        "scenarios": int(meta["Scenarios"]),
        "steps": int(meta["Steps"]),
    }


@pytest.mark.parametrize("scalar_key", ["elements", "steps", "tags"])
def test_a_scalar_where_a_list_belongs_renders_at_200(
    client: FlaskClient, flask_app: Flask, results_root: Path, scalar_key: str
) -> None:
    """A number where a list belongs is a poorer page, never a 500.

    Each of these three documents parses, so the data-availability rule admits
    it, and each used to raise ``TypeError: 'int' object is not iterable``
    inside the overview template - turning ``/reports`` into a 500 while
    ``/reports/summary`` answered 200 with neutral counts for the very same
    file.  Both surfaces must now answer 200 and must agree, which is what
    reading one coercing model rather than iterating raw members gives.
    """
    element: dict[str, Any] = {
        "type": "scenario",
        "keyword": "Scenario",
        "line": 5,
        "name": "a scenario",
        "description": "",
        "steps": [],
    }
    feature: dict[str, Any] = {
        "uri": "file:features/Scalar.feature",
        "keyword": "Feature",
        "line": 1,
        "name": "Scalar member feature",
        "description": "",
        "elements": [element],
    }
    if scalar_key == "elements":
        feature["elements"] = 1
    elif scalar_key == "steps":
        element["steps"] = 1
    else:
        feature["tags"] = 1
        element["tags"] = 1
    _write_results(results_root, [feature])

    response = client.get(_url(flask_app, "web.reports_overview"))

    assert response.status_code == 200, f"{scalar_key} as a scalar took the page down"
    assert _overview_totals(_text_of(response)) == _summary_totals(client, flask_app)
    # The two detail views read the same model, so neither may fail either.
    feature_response = client.get(_url(flask_app, "web.report_feature", findex=0))
    assert feature_response.status_code == 200


def test_a_string_step_list_renders_no_step_and_agrees_with_the_summary(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """``steps: "nope"`` is no steps, not four unknown ones.

    A string is iterable, so a template iterating the raw member rendered one
    phantom step per character - four unknown steps for this value - while
    ``/reports/summary`` counted none.  The model rejects a string outright
    rather than iterating it, so the page shows no step row, its step total is
    zero, and the two surfaces state the same number.
    """
    _write_results(
        results_root,
        [
            {
                "uri": "file:features/Strings.feature",
                "keyword": "Feature",
                "line": 1,
                "name": "String step list feature",
                "description": "",
                "tags": [],
                "elements": [
                    {
                        "type": "scenario",
                        "keyword": "Scenario",
                        "line": 5,
                        "id": "string-step-list-feature;nope",
                        "name": "its step list is a string",
                        "description": "",
                        "start_timestamp": "2022-09-07T13:00:00.000Z",
                        "tags": [],
                        "steps": "nope",
                    }
                ],
            }
        ],
    )

    overview = _text_of(client.get(_url(flask_app, "web.reports_overview")))
    feature_page = _text_of(
        client.get(_url(flask_app, "web.report_feature", findex=0))
    )
    scenario_page = _text_of(
        client.get(_url(flask_app, "web.report_scenario", findex=0, sindex=0))
    )

    totals = _summary_totals(client, flask_app)
    assert totals["steps"] == 0
    assert _overview_totals(overview) == totals
    assert _step_rows(feature_page) == [], "the feature page rendered a phantom step"
    assert _step_rows(scenario_page) == [], "the scenario page rendered a phantom step"
    # The statistics table's own step total is the same reading.
    assert re.search(
        rf'{STATUS_HOOK}="[a-z]+">\s*<th scope="row">.*?</th>'
        r"(?:\s*<td>0</td>){6}",
        overview,
        re.DOTALL,
    ), "the feature's row does not report six zero step counts"


@pytest.mark.parametrize(
    "unusable", ["not-a-timestamp", 12345], ids=["unparseable", "not-a-string"]
)
def test_an_unusable_start_timestamp_is_never_displayed(
    client: FlaskClient, flask_app: Flask, results_root: Path, unusable: Any
) -> None:
    """A timestamp the model cannot parse is shown by no page at all.

    The summary route dropped such a value and answered ``null`` while the
    pages accepted any string lexically and displayed it, so one surface
    reported a run start the other denied.  Every page now shows a timestamp
    only where the model could parse it, which for these two values means the
    em dash the absent state renders.
    """
    _write_results(
        results_root,
        [
            {
                "uri": "file:features/Timestamps.feature",
                "keyword": "Feature",
                "line": 1,
                "name": "Timestamp feature",
                "description": "",
                "tags": [],
                "elements": [
                    {
                        "type": "scenario",
                        "keyword": "Scenario",
                        "line": 5,
                        "id": "timestamp-feature;unusable",
                        "name": "its start timestamp is unusable",
                        "description": "",
                        "start_timestamp": unusable,
                        "tags": [],
                        "steps": [_step("a step", "passed")],
                    }
                ],
            }
        ],
    )

    body = client.get(_url(flask_app, "web.reports_summary")).get_json()
    assert body["start_timestamp"] is None

    for endpoint, values, label in (
        ("web.reports_overview", {}, "First scenario started"),
        ("web.report_feature", {"findex": 0}, "First scenario started"),
        ("web.report_scenario", {"findex": 0, "sindex": 0}, "Started"),
    ):
        page = _text_of(client.get(_url(flask_app, endpoint, **values)))
        shown = _meta_values(page)[label]
        assert "&mdash;" in shown, f"{endpoint} displayed {shown!r}"
        assert str(unusable) not in page, (
            f"{endpoint} rendered the unusable timestamp {unusable!r}"
        )


# --------------------------------------------------------------------------
# Cache policy (CWE-525).  Every response of this surface carries run
# evidence: a report page quotes the step and assertion text of a suite whose
# fixtures are credentials, the scenario page embeds the failure screenshot,
# and the artifact route serves the results file itself.  A run's artifacts
# are removed by the clean step, so a copy kept by a browser or a private
# intermediary outlives what it describes.
#
# no-store rather than no-cache, and the distinction is the finding: RFC 9111
# makes no-cache a revalidation requirement, under which the representation is
# still written to disk and merely checked before reuse.
# --------------------------------------------------------------------------


def _assert_no_store(response: TestResponse, what: str) -> None:
    """Assert one response forbids storage, in both spellings.

    :param response: The response to check.
    :param what: What produced it, for the failure message.
    :raises AssertionError: If either header is absent or weakened.
    """
    assert response.headers.get("Cache-Control") == NO_STORE_POLICY, (
        f"{what} answered Cache-Control "
        f"{response.headers.get('Cache-Control')!r}"
    )
    assert response.headers.get("Pragma") == NO_STORE_PRAGMA, (
        f"{what} answered Pragma {response.headers.get('Pragma')!r}"
    )


@pytest.mark.parametrize(("endpoint", "values"), [
    ("web.index", {}),
    ("web.reports_overview", {}),
    ("web.report_feature", {"findex": 0}),
    ("web.report_scenario", {"findex": 0, "sindex": 0}),
    ("web.reports_summary", {}),
    ("web.artifact", {"name": "cucumber.json"}),
])
def test_every_successful_response_forbids_caching(
    client: FlaskClient,
    flask_app: Flask,
    artifact_tree: Path,
    sample_document: list[dict[str, Any]],
    endpoint: str,
    values: dict[str, Any],
) -> None:
    """All six routes, in the state where each one succeeds.

    Run against a fully served workspace on purpose: a header asserted on a
    404 would prove nothing about the responses that actually carry the
    evidence.  The artifact route is included because its framework default
    was ``no-cache`` alone, which permits storage.
    """
    _fully_served_state(artifact_tree, sample_document)

    response = client.get(_url(flask_app, endpoint, **values))

    assert response.status_code == 200
    _assert_no_store(response, f"{endpoint} {values}")


@pytest.mark.parametrize(("endpoint", "values"), list(REPORT_ENDPOINT_ARGS))
def test_every_report_route_404_forbids_caching(
    client: FlaskClient,
    flask_app: Flask,
    results_root: Path,
    endpoint: str,
    values: dict[str, Any],
) -> None:
    """The data-availability 404, HTML and JSON alike.

    That 404 is the answer for a run whose artifacts have just been removed,
    so a cached copy of the page a reader saw before the removal is exactly
    what must not be kept.
    """
    response = client.get(_url(flask_app, endpoint, **values))

    assert response.status_code == 404
    _assert_no_store(response, f"{endpoint} {values}")


def test_an_unmatched_url_404_forbids_caching(
    client: FlaskClient, results_root: Path
) -> None:
    """An unmatched URL too, which no blueprint hook can reach.

    A request that matches no rule is answered by the application's own error
    handler with no endpoint at all, so the policy has to be stated by
    ``app/errors.py`` as well as by the blueprint - this is the request that
    proves it is.
    """
    response = client.get("/blitzy-no-such-url")

    assert response.status_code == 404
    _assert_no_store(response, "an unmatched URL")


def test_a_json_negotiated_404_forbids_caching(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """The JSON error body, which the handler builds rather than renders."""
    response = client.get(
        _url(flask_app, "web.reports_summary"),
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 404
    assert response.mimetype == "application/json"
    _assert_no_store(response, "the JSON 404")


@handler_app
def test_the_internal_error_response_forbids_caching(
    error_client: FlaskClient, results_root: Path
) -> None:
    """The 500 page, served for a request that broke mid-render.

    Whatever it broke on, the response is a page about a run, and the handler
    that renders it is outside the blueprint - so this is the second body
    ``app/errors.py`` has to state the policy on itself.
    """
    response = error_client.get(PROBE_RULE)

    assert response.status_code == 500
    _assert_no_store(response, "the internal-error page")


@handler_app
def test_a_json_negotiated_500_forbids_caching(
    error_client: FlaskClient, results_root: Path
) -> None:
    """The JSON internal-error body states the policy too.

    ``app/errors.py`` builds three shapes and each one has to carry it: the
    page above, this body, and the plain-text fallback below.  A client that
    asked for JSON gets a JSON 500, and a 500 is rendered for a request that
    broke while handling run evidence, so the storage prohibition cannot
    depend on which shape the negotiation chose.
    """
    response = error_client.get(PROBE_RULE, headers={"Accept": "application/json"})

    assert response.status_code == 500
    assert response.mimetype == "application/json"
    _assert_no_store(response, "the JSON internal-error body")


def test_a_render_failure_still_forbids_caching(
    client: FlaskClient, results_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last-resort plain-text body carries the policy as well.

    The handler guards its own render, because a page that will not render
    must still answer at the right status rather than turning a 404 into a
    500.  That fallback is the one error shape no negotiation reaches, so it
    is provoked directly - the render is made to raise - and it is exactly the
    shape most likely to be served while something is already wrong, which is
    when a cached copy of it would be least welcome.
    """
    from app import errors

    def _fail(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("the error page will not render")

    monkeypatch.setattr(errors, "render_template", _fail)

    response = client.get("/no-such-url-at-all")

    assert response.status_code == 404
    assert response.mimetype == "text/plain"
    _assert_no_store(response, "the plain-text error fallback")


def test_the_packaged_static_asset_stays_cacheable(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """The stylesheet and the script are not run evidence and are not sealed.

    The hook is registered on the blueprint rather than on the application for
    this reason: Flask's own static route carries the shell's stylesheet and
    behaviour script, which describe no run and are the same bytes for every
    reader, so sealing them would cost a fetch per page for no benefit.  What
    must not happen is those two ending up *more* protected than a report -
    the assertion is only that no storage prohibition is claimed there.
    """
    for filename in ("css/main.css", "js/report.js"):
        response = client.get(_url(flask_app, "static", filename=filename))

        assert response.status_code == 200, filename
        assert "no-store" not in (response.headers.get("Cache-Control") or ""), (
            f"{filename} is served with a storage prohibition it does not need"
        )


# --------------------------------------------------------------------------
# GET /artifacts/<path:name> - the allowlist, and every hostile spelling of a
# path that must not be served.
#
# Each rejection is its own test, and each asserts more than a status: the
# canonical 404 page, no filesystem path in the body, and none of the content
# the request was reaching for.  The files it reaches for are created first,
# with distinctive payloads, so a rejection is proved rather than merely
# observed against an empty directory.
# --------------------------------------------------------------------------

def test_artifact_serves_the_generated_html_report(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """The first allowlisted file, by its own content and its media type."""
    response = client.get(_url(flask_app, "web.artifact", name="cucumber-reports.html"))

    assert response.status_code == 200
    assert _text_of(response) == ARTIFACT_CONTENT["cucumber-reports.html"]
    assert response.headers["Content-Type"].startswith("text/html")


def test_artifact_serves_the_results_file(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """The results artifact is downloadable, as JSON, from the route."""
    response = client.get(_url(flask_app, "web.artifact", name="cucumber.json"))

    assert response.status_code == 200
    assert _text_of(response) == ARTIFACT_CONTENT["cucumber.json"]
    assert response.headers["Content-Type"].startswith("application/json")


def test_artifact_serves_the_rerun_manifest(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """The manifest a second runner reads, served byte for byte.

    Its trailing newline is part of the format - a machine reads this file -
    so the comparison is against the exact text rather than a stripped one.
    """
    response = client.get(_url(flask_app, "web.artifact", name="rerun.txt"))

    assert response.status_code == 200
    assert _text_of(response) == ARTIFACT_CONTENT["rerun.txt"]
    assert response.headers["Content-Type"].startswith("text/plain")


def test_artifact_serves_a_page_beneath_the_report_tree(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """Any path under the report tree is allowlisted, not just its overview.

    The tree is generated with one detail page per feature and per tag, and
    every internal link in it points at a sibling page, so the whole subtree
    has to be reachable for the artifact to be usable at all.
    """
    response = client.get(
        _url(flask_app, "web.artifact", name=PRETTY_DETAIL_RELPATH)
    )

    assert response.status_code == 200
    assert _text_of(response) == PRETTY_DETAIL_CONTENT


@pytest.mark.parametrize("spelling", TREE_DIRECTORY_SPELLINGS)
def test_artifact_redirects_the_tree_directory_to_its_overview_url(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path, spelling: str
) -> None:
    """Naming the report tree canonicalizes to the overview page's own URL.

    Both authorized spellings - the bare key the landing page once linked, and
    the slash a reader may type - answer a redirect to the nested page rather
    than the page's bytes, because the bytes alone are not the artifact: the
    generated pages reference their assets and each other relatively, so a
    reader served at the directory alias has a base URL one level too high and
    every one of those references 404s.  Both halves are asserted: the
    destination, and that a relative reference resolved against it reaches the
    asset that is genuinely there.

    Following the redirect must then serve the overview page itself - not a
    listing of the directory, and not another page from the tree.
    """
    response = client.get(_artifact_url(flask_app, spelling))

    assert response.status_code == 302
    destination = _url(
        flask_app, "web.artifact", name=PRETTY_OVERVIEW_RELPATH
    )
    assert response.headers["Location"] == destination
    assert urljoin(destination, "css/cucumber.css") == _url(
        flask_app, "web.artifact", name=PRETTY_ASSET_RELPATH
    ), "a relative asset reference does not resolve under the redirect target"
    assert client.get(urljoin(destination, "css/cucumber.css")).status_code == 200

    followed = client.get(_artifact_url(flask_app, spelling), follow_redirects=True)

    assert followed.status_code == 200
    assert _text_of(followed) == PRETTY_OVERVIEW_CONTENT
    assert PRETTY_DETAIL_CONTENT not in _text_of(followed)
    assert "Index of" not in _text_of(followed)


def test_the_tree_alias_is_not_where_the_overview_page_is_served(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """The alias carries no artifact bytes of its own, at either spelling.

    The defect this pins is not the status code but the base URL: a response
    that carried the page's bytes *at the alias* would render, and every
    relative reference in it would then be requested one directory too high.
    So the redirect body must not be the page, and the URL a relative
    reference would produce from the alias must be one nothing serves.
    """
    for spelling in TREE_DIRECTORY_SPELLINGS:
        alias = _artifact_url(flask_app, spelling)
        response = client.get(alias)

        assert response.status_code == 302
        assert PRETTY_OVERVIEW_CONTENT not in _text_of(response)
        assert client.get(urljoin(alias, "css/cucumber.css")).status_code == 404


@pytest.mark.parametrize("spelling", TREE_DIRECTORY_OVER_SPELLINGS)
def test_artifact_rejects_every_longer_trailing_separator_spelling(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path, spelling: str
) -> None:
    """Two spellings of the tree are authorized; a third slash is not one.

    AAP 0.3.1 authorizes the bare key and exactly one trailing slash, and the
    path module refuses everything else because a second slash leaves an empty
    component behind.  A route that collapsed trailing separators before
    validating - ``rstrip("/")`` - would broaden that allowlist to every
    variant, so each is asserted to be the plain 404 and neither a redirect nor
    a served page.
    """
    canonical = _not_found_page(client, flask_app)

    response = client.get(_artifact_url(flask_app, spelling))

    assert response.location is None, "the rejection redirected instead of refusing"
    _assert_rejected(
        response, canonical=canonical, forbidden=(PRETTY_OVERVIEW_CONTENT,)
    )


def test_artifact_serves_a_results_file_the_report_routes_refuse(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """An unparseable artifact is still a downloadable one.

    The two routes read the same file for different purposes, so this pins the
    difference: the report route answers 404 because it cannot present the
    file, while the artifact route serves its bytes unchanged, because it never
    parses what it serves.
    """
    _install_unusable_results(results_root, "unparseable")
    raw = _results_path(results_root).read_bytes()

    served = client.get(_url(flask_app, "web.artifact", name="cucumber.json"))
    overview = client.get(_url(flask_app, "web.reports_overview"))

    assert served.status_code == 200
    assert served.data == raw
    assert overview.status_code == 404


def test_artifact_rejects_a_name_off_the_allowlist(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """A real file inside the artifact root, not on the allowlist: 404.

    The file exists and has content, so this is the allowlist refusing a name
    rather than the filesystem refusing a path.
    """
    canonical = _not_found_page(client, flask_app)

    response = client.get(_artifact_url(flask_app, UNLISTED_NAME))

    _assert_rejected(
        response, canonical=canonical, forbidden=(UNRELATED_PAYLOAD, UNLISTED_NAME)
    )


def test_artifact_rejects_a_directory_that_is_not_the_report_tree(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """Only one directory is servable, and every other one is a 404.

    No directory listing, no invented index: the response is the same page a
    missing file gets, and it names nothing inside the directory.
    """
    canonical = _not_found_page(client, flask_app)

    response = client.get(_artifact_url(flask_app, UNRELATED_DIR_NAME))

    _assert_rejected(
        response,
        canonical=canonical,
        forbidden=(UNRELATED_PAYLOAD, UNRELATED_DIR_NAME, "blitzy-unrelated-file"),
    )


def test_artifact_rejects_a_file_inside_an_unlisted_directory(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """The allowlist covers one subtree only, so a sibling subtree is refused.

    Asserted separately from the directory itself because the two take
    different paths through the validation: one is refused as a directory, this
    one as a name the allowlist does not carry.
    """
    canonical = _not_found_page(client, flask_app)

    response = client.get(_artifact_url(flask_app, UNRELATED_FILE_NAME))

    _assert_rejected(
        response,
        canonical=canonical,
        forbidden=(UNRELATED_PAYLOAD, UNRELATED_FILE_NAME),
    )


def test_artifact_rejects_an_allowlisted_name_with_nothing_behind_it(
    client: FlaskClient, flask_app: Flask, results_root: Path
) -> None:
    """An allowlisted name that is not on disk is a 404, not an empty 200.

    Run against a root where ``target/`` does not exist at all, which is the
    state a checkout is in before the first run and the one in which every
    link the landing page could offer would otherwise resolve to nothing.
    """
    canonical = _not_found_page(client, flask_app)

    for spec in ARTIFACT_SPECS:
        response = client.get(_url(flask_app, "web.artifact", name=spec.key))
        _assert_rejected(response, canonical=canonical, forbidden=(spec.key,))


def test_artifact_rejects_the_worker_intermediates_directory(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """``.workers`` itself is refused, and the directory really is there.

    The Jenkins publisher's include pattern was narrowed to keep the
    per-worker results out of the published report; serving them over HTTP
    would undo that, so the rejection is asserted against a populated
    directory and its content is checked for in the body.
    """
    assert (artifact_tree / "target" / ".workers").is_dir()
    canonical = _not_found_page(client, flask_app)

    response = client.get(_artifact_url(flask_app, ".workers"))

    _assert_rejected(
        response, canonical=canonical, forbidden=(WORKER_PAYLOAD, ".workers")
    )


def test_artifact_rejects_a_worker_intermediate_file(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """A real worker result file, named exactly, is still a 404.

    The file's presence is asserted first, so the rejection cannot be an
    artefact of an absent path, and its payload is checked for in the body.
    """
    assert (artifact_tree / "target" / WORKER_FILE_NAME).is_file()
    canonical = _not_found_page(client, flask_app)

    response = client.get(_artifact_url(flask_app, WORKER_FILE_NAME))

    _assert_rejected(
        response,
        canonical=canonical,
        forbidden=(WORKER_PAYLOAD, WORKER_FILE_NAME, "worker-1-0000"),
    )


def test_artifact_rejects_parent_directory_traversal(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """``..`` cannot climb out of the artifact root.

    The file above the root exists and carries a distinctive payload, so the
    assertion is that the route declined to serve something it could have
    reached, not that the path was empty.
    """
    assert (artifact_tree / OUTSIDE_FILE_NAME).is_file()
    canonical = _not_found_page(client, flask_app)

    response = client.get(_artifact_url(flask_app, f"../{OUTSIDE_FILE_NAME}"))

    _assert_rejected(
        response, canonical=canonical, forbidden=(OUTSIDE_PAYLOAD, OUTSIDE_FILE_NAME)
    )


def test_artifact_rejects_traversal_out_through_the_report_tree(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """A traversal that starts inside the one allowlisted subtree.

    The dangerous shape: the name begins with the prefix the allowlist admits
    and then climbs out of it, so a check that looked only at the prefix would
    pass it.
    """
    canonical = _not_found_page(client, flask_app)

    response = client.get(
        _artifact_url(flask_app, f"cucumber/../../{OUTSIDE_FILE_NAME}")
    )

    _assert_rejected(
        response, canonical=canonical, forbidden=(OUTSIDE_PAYLOAD, OUTSIDE_FILE_NAME)
    )


@pytest.mark.parametrize("encoded", ["%2e%2e/", "%2e%2e%2f", "%2E%2E/"])
def test_artifact_rejects_percent_encoded_traversal(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path, encoded: str
) -> None:
    """The encoded spellings of the same climb, in both cases.

    The framework decodes the segment before the view sees it, so these arrive
    as a traversal and must be refused as one; a validator that ran before
    decoding would let them through.
    """
    canonical = _not_found_page(client, flask_app)

    response = client.get(_artifact_url(flask_app, f"{encoded}{OUTSIDE_FILE_NAME}"))

    _assert_rejected(
        response, canonical=canonical, forbidden=(OUTSIDE_PAYLOAD, OUTSIDE_FILE_NAME)
    )


def test_artifact_rejects_an_absolute_path(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """An absolute path names nothing this route will serve.

    It arrives at the rule as a doubled separator, which ``merge_slashes=False``
    keeps as a 404 rather than a redirect, so the rejection is the plain page
    and not a 301 to a normalised URL.
    """
    absolute = artifact_tree / OUTSIDE_FILE_NAME
    canonical = _not_found_page(client, flask_app)

    response = client.get(_artifact_url(flask_app, str(absolute)))

    assert response.status_code == 404
    assert response.location is None, "the rejection redirected instead of refusing"
    _assert_rejected(response, canonical=canonical, forbidden=(OUTSIDE_PAYLOAD,))


def test_artifact_rejects_a_doubled_separator(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """A doubled slash is refused outright, not merged into a valid name.

    ``merge_slashes`` is off on this rule precisely so that *everything else*
    means every rejection is the same 404: the allowlisted name behind the
    doubled separator must not be served, and no redirect may be offered.
    """
    canonical = _not_found_page(client, flask_app)

    response = client.get(_artifact_url(flask_app, "/cucumber.json"))

    assert response.location is None, "the rejection redirected instead of refusing"
    _assert_rejected(
        response, canonical=canonical, forbidden=(ARTIFACT_CONTENT["cucumber.json"],)
    )


def test_artifact_rejects_a_backslash_separator(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """A Windows-style separator cannot smuggle a component past validation.

    The path module normalises backslashes to forward slashes before it
    validates, so this arrives as the same traversal as the POSIX spelling and
    is refused the same way.
    """
    canonical = _not_found_page(client, flask_app)

    response = client.get(
        _artifact_url(flask_app, f"..%5c{OUTSIDE_FILE_NAME}")
    )

    _assert_rejected(
        response, canonical=canonical, forbidden=(OUTSIDE_PAYLOAD, OUTSIDE_FILE_NAME)
    )


def test_artifact_rejects_a_symlink_whose_target_escapes_the_root(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """Containment is judged after resolution, so a link out is caught too.

    The link lives inside the one allowlisted subtree and its name ends in
    ``.html``, so nothing about the *request* is suspicious: only resolving it
    reveals that it leaves the artifact root.  The link's reality and its
    target are asserted first, so a broken link cannot make this pass.
    """
    link = artifact_tree / "target" / ESCAPING_LINK_NAME
    assert link.is_symlink()
    assert link.resolve() == (artifact_tree / OUTSIDE_FILE_NAME).resolve()
    assert not link.resolve().is_relative_to(artifact_tree / "target")
    canonical = _not_found_page(client, flask_app)

    response = client.get(_artifact_url(flask_app, ESCAPING_LINK_NAME))

    _assert_rejected(
        response, canonical=canonical, forbidden=(OUTSIDE_PAYLOAD, OUTSIDE_FILE_NAME)
    )


def test_artifact_rejects_a_directory_inside_the_report_tree(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """Only the tree's own key is rewritten to a page; a subdirectory is not.

    Both spellings are refused, so the route neither lists a directory nor
    invents an index for one.
    """
    canonical = _not_found_page(client, flask_app)

    for name in ("cucumber/cucumber-html-reports", "cucumber/cucumber-html-reports/"):
        response = client.get(_artifact_url(flask_app, name))
        _assert_rejected(
            response, canonical=canonical, forbidden=(PRETTY_OVERVIEW_CONTENT,)
        )


def test_artifact_rejects_an_empty_name(
    client: FlaskClient, flask_app: Flask, artifact_tree: Path
) -> None:
    """The route has no bare form: an empty segment matches no rule.

    Asserted because the rule's converter would otherwise be the only thing
    standing between a reader and a directory listing of the artifact root.
    """
    canonical = _not_found_page(client, flask_app)

    response = client.get(_artifact_url(flask_app, ""))

    _assert_rejected(
        response,
        canonical=canonical,
        forbidden=(UNLISTED_NAME, PRETTY_OVERVIEW_CONTENT),
    )


# --------------------------------------------------------------------------
# What the route serves is the object it checked.  The three tests below all
# mutate the artifact *after* validation and before the response is built, by
# wrapping the framework call the route makes last, which is the window a
# check-then-reopen sequence leaves open (CWE-367).  A route that names the
# file a second time serves whatever the name reaches by then; a route that
# holds the descriptor it verified cannot.
# --------------------------------------------------------------------------


def _mutate_before_the_response(
    monkeypatch: pytest.MonkeyPatch, mutate: Any
) -> None:
    """Run ``mutate`` immediately before the artifact response is built.

    The hook is the view module's own reference to the framework's file-sending
    call, which every version of this route reaches only after it has finished
    validating - so the mutation is deterministically post-validation, with no
    sleep, no thread and no race to lose.

    :param monkeypatch: pytest's patcher, for its guaranteed teardown.
    :param mutate: A no-argument callable that changes the filesystem.
    """
    from app.web import routes as view_module

    sender = view_module.send_file

    def _sending(*args: Any, **kwargs: Any) -> Any:
        mutate()
        return sender(*args, **kwargs)

    monkeypatch.setattr(view_module, "send_file", _sending)


def _validated_rerun_manifest(root: Path) -> Path:
    """Put known content at the manifest's path, for the three swap tests.

    :param root: An artifact root already carrying the artifact tree.
    :returns: The manifest's path, carrying
        :data:`VALIDATED_ARTIFACT_CONTENT`.
    """
    path = root / TARGET_DIR_NAME / RERUN_TXT_NAME
    path.write_text(VALIDATED_ARTIFACT_CONTENT, encoding="utf-8")
    return path


def test_artifact_serves_the_object_it_validated_not_the_name(
    client: FlaskClient,
    flask_app: Flask,
    artifact_tree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file replaced after validation is still served as it was validated.

    The pathname is made to address a different object - same name, new inode,
    different content - after the route has finished deciding and before the
    bytes are read.  What comes back must be the bytes of the object that was
    checked, which is only possible if the response streams a descriptor rather
    than reopening the name.  The swap's reality is asserted afterwards, so a
    test that mutated nothing cannot pass.
    """
    path = _validated_rerun_manifest(artifact_tree)

    def _swap() -> None:
        path.unlink()
        path.write_text(SWAPPED_ARTIFACT_CONTENT, encoding="utf-8")

    _mutate_before_the_response(monkeypatch, _swap)

    response = client.get(_url(flask_app, "web.artifact", name=RERUN_TXT_NAME))

    assert response.status_code == 200
    assert _text_of(response) == VALIDATED_ARTIFACT_CONTENT
    assert SWAPPED_ARTIFACT_CONTENT not in _text_of(response)
    assert path.read_text(encoding="utf-8") == SWAPPED_ARTIFACT_CONTENT, (
        "the swap did not happen, so this test proved nothing"
    )


def test_a_post_validation_swap_cannot_disclose_a_file_outside_the_root(
    client: FlaskClient,
    flask_app: Flask,
    artifact_tree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The swap that discloses a file through a reopened name discloses none.

    The strongest form of the window: the validated name is replaced with a
    symbolic link to a file above the artifact root, which is exactly what a
    second open would follow.  The response must carry the validated artifact's
    own bytes and none of the external file's - not a 403, not a 500, and above
    all not the external payload.
    """
    path = _validated_rerun_manifest(artifact_tree)
    outside = artifact_tree / OUTSIDE_FILE_NAME
    assert outside.read_text(encoding="utf-8") == OUTSIDE_PAYLOAD

    def _swap() -> None:
        path.unlink()
        os.symlink(outside, path)

    _mutate_before_the_response(monkeypatch, _swap)

    response = client.get(_url(flask_app, "web.artifact", name=RERUN_TXT_NAME))

    assert response.status_code == 200
    assert _text_of(response) == VALIDATED_ARTIFACT_CONTENT
    assert OUTSIDE_PAYLOAD not in _text_of(response)
    assert path.is_symlink(), "the swap did not happen, so this proved nothing"


def test_an_artifact_that_vanishes_after_validation_is_never_a_500(
    client: FlaskClient,
    flask_app: Flask,
    artifact_tree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file unlinked after validation cannot turn the response into an error.

    Reopening a name that no longer resolves raises, and an exception out of a
    view is a 500 - a sanitized one, but still the viewer reporting a fault
    where the contract has only the artifact and its absence.  With the
    descriptor held there is nothing left to fail: the bytes that were
    validated are served, and the unlinked directory entry changes nothing.
    """
    path = _validated_rerun_manifest(artifact_tree)
    _mutate_before_the_response(monkeypatch, path.unlink)

    response = client.get(_url(flask_app, "web.artifact", name=RERUN_TXT_NAME))

    assert response.status_code != 500
    assert response.status_code == 200
    assert _text_of(response) == VALIDATED_ARTIFACT_CONTENT
    assert not path.exists(), "the unlink did not happen"


def test_a_refused_artifact_request_closes_the_stream_it_opened(
    client: FlaskClient,
    flask_app: Flask,
    artifact_tree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal paths own the descriptor they were handed, and close it.

    The route opens before it has finished judging, so the judgement's refusal
    is on the hook for the stream.  ``abort`` raises, which is precisely how a
    descriptor gets left to the garbage collector on a path a prober can drive
    as often as it likes - so the containment check is made to refuse, and the
    stream the route opened is asserted closed once the response is complete.
    """
    from app.web import routes as view_module

    streams: list[Any] = []
    opener = view_module.open_resolved_artifact

    def _recording(name: str) -> Any:
        opened = opener(name)
        if opened is not None:
            streams.append(opened[1])
        return opened

    monkeypatch.setattr(view_module, "open_resolved_artifact", _recording)
    monkeypatch.setattr(view_module, "_within_artifact_root", lambda path: False)

    response = client.get(_url(flask_app, "web.artifact", name=RERUN_TXT_NAME))

    assert response.status_code == 404
    assert streams, "the route did not open the artifact through the authority"
    assert all(stream.closed for stream in streams), (
        "a refused request left the stream it opened unclosed"
    )


# --------------------------------------------------------------------------
# The read-only guarantee.  AAP 0.3.1 states it three ways - every route is
# synchronous and read-only, none writes to disk, and none starts a run - so it
# is asserted three ways: the method surface, the filesystem, and the two
# service entry points a run would have to go through.
# --------------------------------------------------------------------------


def _fully_served_state(root: Path, document: list[dict[str, Any]]) -> None:
    """Arrange a root in which all six routes answer 200.

    The artifact tree supplies the four artifacts; this puts a real results
    document in place of its placeholder so that the two detail routes have a
    feature at position zero and a scenario at position zero to serve.

    :param root: An artifact root already carrying the artifact tree.
    :param document: The results document to install.
    :raises AssertionError: If that document has no feature carrying a
        scenario, in which case the state would not serve all six routes and
        an assertion about them would be testing the wrong thing.
    """
    first_scenarios = [
        element
        for element in document[0]["elements"]
        if element["type"] == "scenario"
    ]
    assert first_scenarios, "the document's first feature carries no scenario"
    _write_results(root, document)


@pytest.mark.parametrize(("endpoint", "values", "mimetype"), [
    # The five HTML pages of AAP 0.3.1's route table, the one JSON route, and
    # every form the artifact route serves - each with the media type its
    # response must carry.
    ("web.index", {}, "text/html"),
    ("web.reports_overview", {}, "text/html"),
    ("web.report_feature", {"findex": 0}, "text/html"),
    ("web.report_scenario", {"findex": 0, "sindex": 0}, "text/html"),
    ("web.reports_summary", {}, "application/json"),
    ("web.artifact", {"name": "cucumber-reports.html"}, "text/html"),
    ("web.artifact", {"name": "cucumber.json"}, "application/json"),
    ("web.artifact", {"name": "rerun.txt"}, "text/plain"),
    # The report tree's directory alias is absent by requirement: it carries no
    # artifact of its own and answers a redirect to the page below, whose media
    # type is the one that matters.  The redirect itself is asserted in the
    # artifact section.
    ("web.artifact", {"name": PRETTY_OVERVIEW_RELPATH}, "text/html"),
    ("web.artifact", {"name": PRETTY_DETAIL_RELPATH}, "text/html"),
    ("web.artifact", {"name": PRETTY_ASSET_RELPATH}, "text/css"),
])
def test_every_successful_response_carries_the_media_type_it_should(
    client: FlaskClient,
    flask_app: Flask,
    artifact_tree: Path,
    sample_document: list[dict[str, Any]],
    endpoint: str,
    values: dict[str, Any],
    mimetype: str,
) -> None:
    """The content type of every success, not only of the two obvious ones.

    A page that answered ``text/plain`` would render as markup in a browser
    and a results file served as HTML would be displayed rather than
    downloaded, so the media type is part of each route's contract and not
    presentation detail.  Both halves are asserted: the type itself, through
    ``mimetype`` so a charset parameter neither satisfies nor breaks the
    check, and - for the text responses - that a charset is declared, since
    the report vocabulary carries non-ASCII content.

    The artifact route's types come from the framework's guess at the name of
    the file it holds open, which is why each shape it serves is listed: the
    generated report, a results document, a manifest, the report tree's
    overview page, a page beneath the tree and one of the vendored assets those
    pages need.
    """
    _fully_served_state(artifact_tree, sample_document)

    response = client.get(_url(flask_app, endpoint, **values))

    assert response.status_code == 200
    assert response.mimetype == mimetype, (
        f"{endpoint} {values} answered {response.mimetype!r}"
    )
    charset = response.mimetype_params.get("charset", "").lower()
    if mimetype.startswith("text/"):
        # Every textual response must name its encoding: the report vocabulary
        # carries non-ASCII content - the French validation message among it -
        # and a text response without a charset is decoded by a browser's
        # guess rather than by declaration.
        assert charset == "utf-8", (
            f"{endpoint} {values} declared charset {charset!r} in "
            f"{response.headers.get('Content-Type')!r}"
        )
    else:
        # JSON is UTF-8 by definition, so a charset parameter is optional
        # there; what must not happen is a *different* one being claimed.
        assert charset in {"", "utf-8"}, (
            f"{endpoint} {values} claims charset {charset!r} for JSON"
        )


@pytest.mark.parametrize(("endpoint", "values"), [
    ("web.index", {}),
    ("web.reports_overview", {}),
    ("web.report_feature", {"findex": 0}),
    ("web.report_scenario", {"findex": 0, "sindex": 0}),
    ("web.reports_summary", {}),
    ("web.artifact", {"name": "cucumber.json"}),
])
def test_no_route_accepts_a_write_method(
    client: FlaskClient,
    flask_app: Flask,
    artifact_tree: Path,
    sample_document: list[dict[str, Any]],
    endpoint: str,
    values: dict[str, Any],
) -> None:
    """Each of the six answers 405 to POST, PUT, PATCH and DELETE.

    Run in the state where every route succeeds for GET, so a 405 cannot be
    mistaken for a route that was failing anyway, and the ``Allow`` header is
    read on every rejection: a surface that advertised a write method it does
    not implement would be a contract defect even while refusing the request.
    """
    _fully_served_state(artifact_tree, sample_document)
    url = _url(flask_app, endpoint, **values)
    assert client.get(url).status_code == 200

    _assert_read_only_methods(client, url)


def test_exercising_every_route_changes_nothing_on_disk(
    client: FlaskClient,
    flask_app: Flask,
    artifact_tree: Path,
    sample_document: list[dict[str, Any]],
) -> None:
    """The whole surface, exercised, leaves the tree exactly as it was.

    Every path under the artifact root is recorded with its kind, size and
    modification time before and after all six routes are requested, so a
    route that created a directory, rewrote an artifact, cached a rendered page
    beside it or replaced a symlink with its target would show up as a
    difference.  The statuses are asserted too, because a snapshot that matched
    because nothing ran would prove nothing.
    """
    _fully_served_state(artifact_tree, sample_document)
    before = _tree_snapshot(artifact_tree)

    statuses = [client.get(url).status_code for url in _every_route_url(flask_app)]

    assert statuses == [200] * len(WEB_ENDPOINT_RULES)
    assert _tree_snapshot(artifact_tree) == before


def test_url_map_carries_exactly_the_six_rules_and_the_static_route(
    flask_app: Flask,
) -> None:
    """Six rules, plus the framework's static route, and no seventh.

    The rule strings and the endpoint names are both asserted, because the
    templates build every URL from the names and a reader reaches the site by
    the rules, so a change to either is a breaking change.  Each rule's method
    set is checked as well: a read-only surface has no rule that accepts more
    than GET, and HEAD and OPTIONS are derived from it by the framework.
    """
    rules = {
        (str(rule), rule.endpoint): frozenset(rule.methods or frozenset())
        for rule in flask_app.url_map.iter_rules()
    }

    expected = {
        (rule, endpoint) for endpoint, rule in WEB_ENDPOINT_RULES
    } | {("/static/<path:filename>", "static")}
    assert set(rules) == expected, (
        f"unexpected rules: {sorted(set(rules) - expected)}; "
        f"missing: {sorted(expected - set(rules))}"
    )
    for key, methods in rules.items():
        assert methods == READ_METHODS, f"{key} accepts {sorted(methods)}"


def test_no_route_can_start_a_run(
    client: FlaskClient,
    flask_app: Flask,
    artifact_tree: Path,
    sample_document: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two entry points a run would have to pass through are never called.

    ``run_suite`` executes the suite and ``generate_reports`` writes the four
    artifacts; between them they are the whole of what "starting a run" means
    in this port.  Both are replaced with sentinels that fail the test if they
    are called at all, and then every route is exercised in a state where each
    one succeeds - so the proof covers the successful paths, which are the ones
    that do the most work, rather than only the rejections.
    """
    calls: list[str] = []

    def _forbidden(name: str) -> Any:
        def _sentinel(*args: Any, **kwargs: Any) -> Any:
            calls.append(name)
            raise AssertionError(f"a route called {name}")

        return _sentinel

    monkeypatch.setattr(
        "app.services.test_run_service.run_suite", _forbidden("run_suite")
    )
    monkeypatch.setattr(
        "app.services.report_service.generate_reports",
        _forbidden("generate_reports"),
    )
    _fully_served_state(artifact_tree, sample_document)

    statuses = [client.get(url).status_code for url in _every_route_url(flask_app)]

    assert statuses == [200] * len(WEB_ENDPOINT_RULES)
    assert calls == []


@pytest.mark.parametrize(
    "requested",
    [CUCUMBER_JSON_NAME, *TREE_DIRECTORY_OVER_SPELLINGS],
)
def test_the_artifact_route_delegates_to_the_path_modules_opener(
    client: FlaskClient,
    flask_app: Flask,
    artifact_tree: Path,
    monkeypatch: pytest.MonkeyPatch,
    requested: str,
) -> None:
    """The allowlist has one owner, and this route consults it by opening.

    AAP 0.4.2 makes ``app/utils/paths.py`` the sole owner of every artifact
    path, and ``tests/test_paths.py`` unit-tests its rules directly.  What
    belongs here is the edge: that the route asks it, and that what it asks
    with is the request's own segment.  The opener - the operation that
    validates and opens together, which is what leaves no window between the
    two - is replaced with a recorder that answers ``None``, and a request for
    an artifact that genuinely exists on disk is then refused, which can only
    happen if the decision came from that function rather than from a join or a
    stat this module performed itself.

    The doubled and tripled trailing separators are parametrized alongside the
    plain name because every rejection rule is expressed over the raw string: a
    route that trimmed separators before asking would hand the authority a name
    it never received, which is how those spellings came to be served.
    """
    from app.web import routes as view_module

    asked: list[str] = []

    def _recording_opener(name: str) -> None:
        asked.append(name)
        return None

    monkeypatch.setattr(view_module, "open_resolved_artifact", _recording_opener)

    response = client.get(_artifact_url(flask_app, requested))

    assert (artifact_tree / RESULTS_RELPATH).is_file(), (
        "the results file must exist, or the refusal below would prove nothing"
    )
    assert response.status_code == 404
    assert asked == [requested], (
        f"the authority was asked {asked} for a request naming {requested!r}"
    )


@pytest.mark.parametrize("spelling", TREE_DIRECTORY_SPELLINGS)
def test_the_tree_alias_asks_the_authority_with_the_spelling_it_received(
    client: FlaskClient,
    flask_app: Flask,
    artifact_tree: Path,
    monkeypatch: pytest.MonkeyPatch,
    spelling: str,
) -> None:
    """The two authorized directory spellings are delegated raw as well.

    The redirect these two answer with is built from the path module's
    constants, which makes it easy for the route to *also* start asking the
    authority about that destination instead of about the request - and then
    the alias decision would be made twice, once in each module, with only one
    of them documenting it.  So this pins the delegation rather than the
    destination: the opener must be asked with the byte-exact segment that
    arrived, trailing separator and all, and it is the authority's own
    allowlist that maps it to the page inside the tree.

    Both halves are asserted, because either alone would pass while the other
    broke: the argument the authority received, and - with the authority back
    in place - the 302 that the request still answers with.

    :param spelling: One of :data:`TREE_DIRECTORY_SPELLINGS`.
    """
    from app.web import routes as view_module

    asked: list[str] = []

    def _recording_opener(name: str) -> None:
        asked.append(name)
        return None

    # A context of its own, not this test's shared patcher: the artifact-root
    # fixture establishes the working directory through that same patcher, so
    # undoing it here would move the request off the populated root as well
    # and the second half would assert nothing.
    with monkeypatch.context() as patched:
        patched.setattr(view_module, "open_resolved_artifact", _recording_opener)
        refused = client.get(_artifact_url(flask_app, spelling))
    redirected = client.get(_artifact_url(flask_app, spelling))

    assert asked == [spelling], (
        f"the authority was asked {asked} for a request naming {spelling!r}"
    )
    assert refused.status_code == 404, (
        "a refusing authority must refuse the alias too, redirect or not"
    )
    assert refused.location is None
    assert redirected.status_code == 302
    assert redirected.headers["Location"] == _url(
        flask_app, "web.artifact", name=PRETTY_OVERVIEW_RELPATH
    )


def test_the_view_module_holds_no_artifact_path_literal(repo_root: Path) -> None:
    """Not one path of its own: the module names routes and templates only.

    Asserted over the parsed module with docstrings excluded, because this
    file's own prose names ``target/``, ``cucumber.json`` and the rest in order
    to talk about them, so a substring search over the source would match the
    documentation rather than the code.  What survives the filter is every
    string the module actually evaluates; the six route rules and the four
    Jinja loader names are the whole of the legitimate set, and an artifact
    path appearing among them would mean this module had started to compute a
    location the path module owns (AAP 0.4.2).
    """
    import ast

    source = (repo_root / "app" / "web" / "routes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    docstring_nodes = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        )
        and ast.get_docstring(node, clean=False) is not None
    }
    evaluated = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_nodes
    ]

    allowed = {rule for rule, _ in WEB_ENDPOINT_RULES} | {
        "index.html",
        "view/overview.html",
        "view/feature.html",
        "view/scenario.html",
    }
    forbidden = (
        TARGET_DIR_NAME,
        CUCUMBER_JSON_NAME,
        RERUN_TXT_NAME,
        CUCUMBER_REPORTS_HTML_NAME,
        WORKERS_DIR_NAME,
    )
    offending = [
        literal
        for literal in evaluated
        if literal not in allowed
        and any(token in literal for token in forbidden)
    ]

    assert offending == [], f"the view module carries path literals {offending}"
    # And the names it does use come from the path module, so the six rules
    # above are the only locations this module spells out at all.  The two
    # openers are named here as well as the accessor: they are how this module
    # turns a location into bytes, and a module that had grown its own read
    # would no longer be holding them.
    from app.web import routes as view_module

    assert view_module.open_resolved_artifact is open_resolved_artifact
    assert view_module.open_artifact_read is open_artifact_read
    assert view_module.cucumber_json_path is cucumber_json_path


def test_every_viewer_template_builds_its_urls_through_url_for(
    repo_root: Path,
) -> None:
    """No template constructs a URL by hand - AAP 0.3.1 says so explicitly.

    A hand-written ``/reports/features/3`` would survive a rule change that
    ``url_for`` would have caught, and would put a page at odds with the route
    table this module pins.  Every ``href``, ``src`` and ``action`` in the
    template tree is therefore checked for an absolute site path: what
    ``url_for`` produces is emitted through a Jinja expression, so a literal
    beginning with ``/`` and carrying no ``{`` is by construction hand-built.
    Scheme-qualified and protocol-relative references are excluded from the
    check, since the two generated HTML artifacts are a separate contract and
    neither is reached through this route table.
    """
    hand_built: list[str] = []
    templates = sorted((repo_root / "app" / "templates").rglob("*.html"))

    assert templates, "no templates were found to check"
    for template in templates:
        for value in re.findall(
            r'(?:href|src|action)\s*=\s*"([^"]*)"',
            template.read_text(encoding="utf-8"),
        ):
            if value.startswith("/") and not value.startswith("//") and "{" not in value:
                hand_built.append(f"{template.relative_to(repo_root)}: {value}")

    assert hand_built == [], f"hand-built URLs found: {hand_built}"


def test_the_view_module_reaches_no_service_writer_or_browser_binding(
    flask_app: Flask,
) -> None:
    """"No route can start a run" is a property of the import graph, and the
    ONE normalized result model is the single exception to it.

    Asserted over the view module's own namespace rather than over
    ``sys.modules``, which any other test in the session can populate: the
    names ``app/web/routes.py`` bound at import time are what its view
    functions can reach.

    Two edges are permitted and no third.  ``app/utils`` owns the paths, and
    :data:`PERMITTED_REPORTING_EDGE` - ``app.reporting.aggregation`` - owns
    every status, tally, duration and timestamp any report surface shows.  The
    second edge exists because AAP 0.4.2 requires it: *"Both HTML artifacts and
    the HTTP views render over one normalized result model ... so no view
    contradicts an artifact"*, and a viewer holding a private copy of that
    calculation cannot satisfy it - a failed after-hook read *passed* in the
    viewer and *failed* in the artifacts for exactly that reason.  That module
    is pure computation: it resolves no path, opens no file, writes nothing and
    can start nothing.

    Everything that *can* act stays out, and that is what this test protects:
    the service layer, every report WRITER (the event collector, the four
    artifact writers and the screenshot module), the page objects, the browser
    automation package, selenium and behave.
    """
    from app.web import routes as view_module

    forbidden = (
        "app.services",
        "app.reporting",
        "app.pages",
        "app.automation",
        "selenium",
        "behave",
    )
    offending: list[str] = []
    for name, value in vars(view_module).items():
        # A bound module answers with its own dotted name; a function, class or
        # instance answers with the module that defined it.  Both forms of
        # edge - "import app.reporting.events" and "from app.reporting.events
        # import x" - are therefore visible here.
        origin = (
            value.__name__
            if isinstance(value, ModuleType)
            else getattr(value, "__module__", None) or ""
        )
        if origin == PERMITTED_REPORTING_EDGE:
            continue
        if any(origin.startswith(prefix) for prefix in forbidden):
            offending.append(f"{name} from {origin}")

    assert offending == [], f"the view module reaches {offending}"

    # The permitted edge is permitted by name and not by prefix, so a writer
    # module cannot ride in beside it.  Each of these is a module the viewer
    # must never reach, and each is checked as its own name rather than as the
    # package prefix above, which the exception now punches a hole in.
    reached = {
        (
            value.__name__
            if isinstance(value, ModuleType)
            else getattr(value, "__module__", None) or ""
        )
        for value in vars(view_module).values()
    }
    for writer in FORBIDDEN_REPORTING_MODULES:
        assert writer not in reached, f"the view module reaches {writer}"

    # The namespace asserted above is the namespace the application actually
    # serves from, and the binding runs in one direction only: ``app/web`` owns
    # the blueprint and calls this module's ``register_routes`` with it, while
    # this module imports nothing from its own package - which is why the
    # reciprocal import a decorator would need does not exist.  Both halves
    # are checked, because either alone would pass while the surface came from
    # somewhere else: the blueprint the factory registered is the one
    # ``app/web`` constructed, and every one of the six endpoints resolves to
    # the view function defined in this module.
    from app.web import bp, web_bp

    registered = flask_app.blueprints["web"]
    assert registered is web_bp
    assert bp is web_bp, "the package's two spellings must be one blueprint"
    assert not hasattr(view_module, "web_bp"), (
        "the view module must not hold a blueprint of its own: it binds its "
        "views through register_routes(blueprint)"
    )
    assert view_module.register_routes.__module__ == view_module.__name__

    for endpoint, _rule in WEB_ENDPOINT_RULES:
        view_name = endpoint.split(".", 1)[1]
        assert flask_app.view_functions[endpoint] is getattr(view_module, view_name), (
            f"{endpoint} is served by something other than {view_name} in "
            f"{view_module.__name__}"
        )
