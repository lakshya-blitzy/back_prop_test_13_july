"""Tests for HTML contract 2: the emitted PrettyReports tree.

This module is the gate for ``app/reporting/pretty_reports.py`` and for every
template under ``app/templates/pretty/`` that writer renders, read as the
*artifact* it produces rather than as the functions that produce it.  The
artifact is a directory -- ``target/cucumber/cucumber-html-reports/`` -- that a
Jenkins agent opens straight from a workspace over the file protocol, so the
contract it is held to here is:

1. **Exact filenames.**  The detail pages are named by a numeric hash the Java
   generator computed (``net.masterthought.cucumber.util.Util.toValidFileName``
   over a feature's ``file:`` URI or a tag's ``@``-prefixed name), so every
   expected name in this module is a pinned literal rather than a pattern.  A
   filename nobody can predict is a filename no published report can link.
2. **Page cardinality.**  The four overview pages, always; one feature page per
   emitted feature in source order; one tag page per tag of the run.
3. **The asset census.**  Exactly 22 files: the 20 vendored ones copied byte
   for byte out of ``app/static/vendor`` plus the port's own ``css/main.css``
   and ``js/report.js``.  Nothing more (a page that links nothing extra), and
   nothing fewer (a page that links a missing file is a broken artifact).
4. **Offline resolution.**  Every ``href`` and ``src`` in every page resolves
   to a file inside the tree, with no external, absolute or protocol-relative
   reference anywhere, and the vendored stylesheets' ``url(../fonts/<name>)``
   requests resolve too.
5. **Content, escaping and aggregation.**  Each emitted feature, tag, failure
   message, timestamp, embedded screenshot, status and duration reaches the
   page that owns it, and hostile result data is escaped rather than executed.
6. **Behaviour at the edges.**  An empty document, a document that shrinks
   between two renders, a render fault and an I/O fault part-way through the
   page loop.

Two seams these tests were written to straddle, both of them now landed.

``write_pretty_reports`` publishes a validated staging tree by rename
    The writer used to overwrite the tree in place and delete nothing, so a
    second render with a smaller result set left the previous run's surplus
    detail pages on disk.  It now builds the whole tree in a dot-prefixed
    staging sibling, verifies the inventory there, and swaps it into place with
    two renames -- so a reader sees one complete generation or none, never a
    mixture, and a page whose feature or tag has disappeared is gone rather
    than merely unreachable.

    The consequence for this module is that a **fault no longer leaves a
    partial tree to inspect**: the three forced-failure tests below assert what
    the destination holds after a fault, which is either the previous complete
    tree byte for byte or nothing at all, and no publication scratch either
    way.  Everything else was already written to hold on both sides of the
    change and did: current pages carry current content, the census stays
    complete, no reference anywhere in the tree dangles, and no page links
    anything the current document did not produce.

The status hook attribute has been unified onto ``data-report-*``
    The pages carry a single ``data-report-status`` hook, and the shared
    partials carry ``data-report-screenshot``, ``data-report-lightbox`` and
    the rest.  Status is still asserted through
    ``partials/status_badge.html``'s stable output rather than through a hook
    name -- the ``tqa-badge`` span whose text content is the token capitalised
    -- because that is the half of the contract a reader sees; where an
    attribute has to be read at all, :data:`STATUS_HOOK_ATTRIBUTES` accepts
    either spelling and so is indifferent to the migration.

Everything is parsed with :mod:`html.parser` from the standard library.
``requirements-test.txt`` pins pytest and pytest-cov and nothing else, and an
HTML artifact contract is not a reason to add a dependency to a test suite.

Fixtures come from ``tests/conftest.py`` and are never redefined here:
``sample_result_set`` (the shared four-feature document), ``tmp_artifact_root``
(an empty checkout root whose ``target/`` is deliberately absent, passed as
``base=``) and ``prepared_artifact_root`` (the same with ``target/`` present).
"""

from __future__ import annotations

import base64
import errno
import hashlib
import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Final

import pytest
from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from app.reporting import pretty_reports
from app.utils import paths

# --------------------------------------------------------------------------
# The page set
# --------------------------------------------------------------------------

#: The four overview pages, in the order the writer renders and writes them.
#: They are the four navigation targets every page's layout links, so the order
#: is part of the contract rather than an implementation detail.
OVERVIEW_PAGES: Final[tuple[str, ...]] = (
    "overview-features.html",
    "overview-tags.html",
    "overview-steps.html",
    "overview-failures.html",
)

#: The overview page a request naming the artifact directory itself is served.
OVERVIEW_INDEX: Final[str] = "overview-features.html"

#: Prefix and suffix of the two detail-page shapes.
FEATURE_PAGE_PREFIX: Final[str] = "report-feature_"
TAG_PAGE_PREFIX: Final[str] = "report-tag_"
PAGE_SUFFIX: Final[str] = ".html"

#: The name of the emitted tree's own directory, and of its parent.  AAP 0.3.4
#: places the pages and assets one level below the plugin's output directory.
TREE_DIR_NAME: Final[str] = "cucumber-html-reports"
TREE_PARENT_DIR_NAME: Final[str] = "cucumber"
BUILD_OUTPUT_DIR_NAME: Final[str] = "target"

# --------------------------------------------------------------------------
# The pinned hashes
#
# Every value below was produced by the writer in this checkout and is asserted
# as a literal, because AAP 0.3.4 requires exact filenames "rather than merely
# numeric-looking ones".  The hash input is the FULL ``file:``-prefixed URI for
# a feature and the tag text INCLUDING its leading ``@`` for a tag.
# --------------------------------------------------------------------------

#: Every feature file of this suite mapped to its detail-page filename.  All
#: ten are pinned, not only the four the sample document carries, because the
#: filename rule has to hold for the whole suite a full run emits.
SUITE_FEATURE_PAGES: Final[dict[str, str]] = {
    "Calendar.feature": "report-feature_2680591225.html",
    "Contact.feature": "report-feature_2292591987.html",
    "Crm.feature": "report-feature_1364259633.html",
    "EmployeeFc.feature": "report-feature_3709499974.html",
    "Inventory.feature": "report-feature_3306537903.html",
    "Login.feature": "report-feature_2592654012.html",
    "Logout.feature": "report-feature_3994017477.html",
    "Notes.feature": "report-feature_459033108.html",
    "Sales.feature": "report-feature_2970536287.html",
    "Session.feature": "report-feature_2509026793.html",
}

#: The URI prefix the port's result model carries, per AAP deviation 1: the
#: feature directory moved to ``features/`` and the filenames were preserved.
FEATURE_URI_PREFIX: Final[str] = "file:features/"

#: The four features the sample document carries, in its own source order,
#: as ``(uri, detail page filename)``.
SAMPLE_FEATURE_PAGES: Final[tuple[tuple[str, str], ...]] = (
    (f"{FEATURE_URI_PREFIX}Contact.feature", SUITE_FEATURE_PAGES["Contact.feature"]),
    (f"{FEATURE_URI_PREFIX}Crm.feature", SUITE_FEATURE_PAGES["Crm.feature"]),
    (
        f"{FEATURE_URI_PREFIX}Inventory.feature",
        SUITE_FEATURE_PAGES["Inventory.feature"],
    ),
    (f"{FEATURE_URI_PREFIX}Sales.feature", SUITE_FEATURE_PAGES["Sales.feature"]),
)

#: The one tag of the sample run, and its page.  Tag text does not move with
#: the feature directory, so this filename is the reference tree's own.
SMOKE_TAG: Final[str] = "@Smoke"
SMOKE_TAG_PAGE: Final[str] = "report-tag_4059758862.html"

#: A tag the sample document carries only on an *unselected* scenario.  It
#: never ran, so it gets no row and no page.
UNSELECTED_TAG: Final[str] = "@wip"

#: The whole expected page set for the sample document, in write order.
SAMPLE_PAGES: Final[tuple[str, ...]] = (
    *OVERVIEW_PAGES,
    *(page for _uri, page in SAMPLE_FEATURE_PAGES),
    SMOKE_TAG_PAGE,
)

#: ``java.lang.Integer.MAX_VALUE``, added to every hash so no filename carries
#: a minus sign -- except for the one input that hashes to ``Integer.MIN_VALUE``
#: and therefore lands on ``-1``, which is reproduced rather than papered over.
INT32_MAX: Final[int] = 2147483647

#: Inputs whose Java ``String.hashCode`` is a fixed point of the algorithm's
#: documented edge cases, as ``(text, signed hash, offset filename segment)``.
HASH_EDGE_CASES: Final[tuple[tuple[str, int, str], ...]] = (
    ("", 0, "2147483647"),
    (SMOKE_TAG, 1912275215, "4059758862"),
    # The Integer.MIN_VALUE case: the JVM's 32-bit overflow is load-bearing,
    # and a mask-only implementation would answer 4294967295 here.
    ("polygenelubricants", -2147483648, "-1"),
)

# --------------------------------------------------------------------------
# The asset census
#
# 22 files: 20 vendored plus the port's own two.  Declared group by group so a
# failure names the group that lost a file, and compared as one frozen set so a
# surplus file fails just as loudly as a missing one.
# --------------------------------------------------------------------------

#: The three vendored stylesheets.
VENDORED_CSS_ASSETS: Final[tuple[str, ...]] = (
    "css/bootstrap.min.css",
    "css/cucumber.css",
    "css/font-awesome.min.css",
)

#: The five vendored scripts.  ``Chart.min.js`` carries a capital C: a
#: lower-case copy is a 404 on every case-sensitive filesystem, which is every
#: Linux build agent.
VENDORED_JS_ASSETS: Final[tuple[str, ...]] = (
    "js/Chart.min.js",
    "js/bootstrap.min.js",
    "js/jquery.min.js",
    "js/jquery.tablesorter.min.js",
    "js/moment.min.js",
)

#: The eleven icon-font files the two vendored stylesheets request as
#: ``url(../fonts/<name>)``.
VENDORED_FONT_ASSETS: Final[tuple[str, ...]] = (
    "fonts/FontAwesome.otf",
    "fonts/fontawesome-webfont.eot",
    "fonts/fontawesome-webfont.svg",
    "fonts/fontawesome-webfont.ttf",
    "fonts/fontawesome-webfont.woff",
    "fonts/fontawesome-webfont.woff2",
    "fonts/glyphicons-halflings-regular.eot",
    "fonts/glyphicons-halflings-regular.svg",
    "fonts/glyphicons-halflings-regular.ttf",
    "fonts/glyphicons-halflings-regular.woff",
    "fonts/glyphicons-halflings-regular.woff2",
)

#: The vendored image: the shortcut icon every page's layout links.
VENDORED_IMAGE_ASSETS: Final[tuple[str, ...]] = ("images/favicon.png",)

#: All 20 vendored files, which must be byte-identical to the pinned generator
#: output under ``app/static/vendor``.
VENDORED_ASSETS: Final[tuple[str, ...]] = (
    *VENDORED_CSS_ASSETS,
    *VENDORED_JS_ASSETS,
    *VENDORED_FONT_ASSETS,
    *VENDORED_IMAGE_ASSETS,
)

#: The port's own two assets, as ``(source under app/static, tree-relative
#: destination)``.  The layout links both, so omitting either ships every page
#: with a broken reference.
PORT_ASSETS: Final[tuple[tuple[str, str], ...]] = (
    ("css/main.css", "css/main.css"),
    ("js/report.js", "js/report.js"),
)

#: The complete census: exactly these 22 tree-relative paths, no more and no
#: fewer.
EXPECTED_ASSETS: Final[frozenset[str]] = frozenset(
    (*VENDORED_ASSETS, *(destination for _source, destination in PORT_ASSETS))
)

#: The four asset sub-directories of the emitted tree.
ASSET_SUBDIRECTORIES: Final[tuple[str, ...]] = ("css", "js", "fonts", "images")

#: Every asset an emitted page links by name, so a page's own reference list
#: can be compared against a fixed expectation rather than against itself.
PAGE_LINKED_ASSETS: Final[frozenset[str]] = frozenset(
    {
        "css/bootstrap.min.css",
        "css/cucumber.css",
        "css/font-awesome.min.css",
        "css/main.css",
        "images/favicon.png",
        "js/Chart.min.js",
        "js/bootstrap.min.js",
        "js/jquery.min.js",
        "js/jquery.tablesorter.min.js",
        "js/moment.min.js",
        "js/report.js",
    }
)

# --------------------------------------------------------------------------
# Reference classification
# --------------------------------------------------------------------------

#: The only in-page fragment any emitted page carries: the Bootstrap carousel
#: control pair on the features overview.  A fragment names a position in the
#: page itself, so it is excluded from the filesystem walk and asserted
#: separately rather than treated as a missing file.
IN_PAGE_FRAGMENTS: Final[frozenset[str]] = frozenset({"#featureChartCarousel"})

#: Prefixes that would take a reader off the tree.  A page carrying any of them
#: is not openable from a CI workspace, which is the whole point of copying the
#: assets in.
EXTERNAL_REFERENCE_PREFIXES: Final[tuple[str, ...]] = (
    "http://",
    "https://",
    "//",
    "file:",
    "ftp://",
    "mailto:",
)

#: The single data-URI scheme the pages legitimately carry: an embedded
#: screenshot.  It references nothing outside the page and is excluded from the
#: filesystem walk for that reason.
DATA_URI_PREFIX: Final[str] = "data:"

#: The screenshot embedding's declared type, as ``app/reporting/events.py``
#: records it and the lightbox partial emits it.
PNG_DATA_URI_PREFIX: Final[str] = "data:image/png;base64,"

#: The three markers a conflicted merge leaves behind.  The committed reference
#: tree carries them (AAP 0.3.4 records nine in one page), which is exactly why
#: a freshly written page is asserted to carry none.
CONFLICT_MARKERS: Final[tuple[str, ...]] = ("<<<<<<<", "=======", ">>>>>>>")

# --------------------------------------------------------------------------
# Status presentation
# --------------------------------------------------------------------------

#: The class ``partials/status_badge.html`` puts on every badge it emits.
BADGE_CLASS: Final[str] = "tqa-badge"

#: The status attribute spellings a badge may carry.  ``data-tqa-status`` is
#: what the pages emit today; the shared-partials owner is migrating every
#: status-bearing element onto ``data-report-status``.  Both are accepted so
#: this module gates the status *vocabulary* without pinning the hook's name,
#: which is another unit's contract.
STATUS_HOOK_ATTRIBUTES: Final[tuple[str, ...]] = (
    "data-tqa-status",
    "data-report-status",
)

#: Every status token the result model can produce, plus the fallback, mapped
#: to the badge label the partial renders for it.  The label is the badge's
#: text content and therefore its accessible name.
STATUS_LABELS: Final[dict[str, str]] = {
    "passed": "Passed",
    "failed": "Failed",
    "skipped": "Skipped",
    "pending": "Pending",
    "undefined": "Undefined",
    "untested": "Untested",
    "ambiguous": "Ambiguous",
    "unknown": "Unknown",
}

#: A status the result model never produces, used to drive the fallback.  The
#: writer must report it as ``unknown`` rather than as a pass.
UNRECOGNISED_STATUS: Final[str] = "executing"

# --------------------------------------------------------------------------
# Fixed values of the sample document
#
# Measured from ``tests/fixtures/sample_results.json`` rendered through this
# writer.  They are what the content assertions below look for.
# --------------------------------------------------------------------------

#: The already-formatted Date cell the sample document produces on its own: it
#: carries ``generated_at``, so two renders of it agree without any pinning.
SAMPLE_BUILD_DATE: Final[str] = "07 Sep 2022, 13:39"

#: A build date passed explicitly where a test needs the value it asserts on to
#: be its own rather than the document's.
FIXED_BUILD_DATE: Final[str] = "02 Jan 2026, 03:04"

#: The four feature names the sample document carries, in source order.  The
#: first and third are identical: Contact and Inventory share a title, and
#: therefore share the ``id`` derived from it.
SAMPLE_FEATURE_NAMES: Final[tuple[str, ...]] = (
    "Testinium app Inventory feature",
    "Testinium app CRM Module",
    "Testinium app Inventory feature",
    ".... app Sales feature",
)

#: The ``id`` Contact and Inventory both carry.  A detail page is named from
#: the URI and not from the id, which is what keeps their pages distinct.
SHARED_FEATURE_ID: Final[str] = "testinium-app-inventory-feature"

#: The subject of each of the sample document's two failure messages.  Asserted
#: rather than the whole text: AAP deviation 16 makes the Python traceback
#: around them non-parity, while the subject and message are.
SAMPLE_FAILURE_SUBJECTS: Final[tuple[str, ...]] = (
    "The title is not same as the expected!",
    "no such element: Unable to locate element",
)

#: One scenario start timestamp of the sample document, carried verbatim from
#: the result model onto the pages that show a scenario.
SAMPLE_START_TIMESTAMP: Final[str] = "2022-09-07T13:37:48.844Z"

#: Step implementations of the sample document with the occurrence count the
#: steps overview aggregates for them.
SAMPLE_STEP_OCCURRENCES: Final[tuple[tuple[str, int], ...]] = (
    ("features.steps.session_steps.user_login_to_test_other_features", 4),
    ("features.steps.sales_steps.user_click_on_the_sales_dashboard", 3),
    ("features.steps.crm_steps.user_can_change_any_user_s_information", 2),
    ("features.steps.contacts_steps.user_sees_deleted_profile", 1),
)

#: Durations as the macros render them from nanosecond integers: seconds to
#: three decimal places, with minutes split out above sixty seconds.  The
#: middle value is a step whose duration is exactly ``0``.
SAMPLE_DURATIONS: Final[tuple[str, ...]] = ("7.393", "0.000", "2:16.311", "20.477")

# --------------------------------------------------------------------------
# Hostile values
#
# One string per model position that reaches a page, each carrying an element,
# an attribute break, an entity and a Jinja expression.  ``{{ 7*7 }}`` is the
# load-bearing one: finding ``49`` on a page would mean result data had been
# evaluated as a template.
# --------------------------------------------------------------------------

HOSTILE_FEATURE_NAME: Final[str] = '<script>alert("f")</script> & {{ 7*7 }}'
HOSTILE_SCENARIO_NAME: Final[str] = 'Sce "q" & <i>x</i> {{ 7*7 }}'
HOSTILE_TAG: Final[str] = "@<script>alert('t')</script>"
HOSTILE_STEP_NAME: Final[str] = 'User types "<script>alert(1)</script>" & waits'
HOSTILE_ARGUMENT: Final[str] = '"<script>alert(1)</script>"'
HOSTILE_ARGUMENT_OFFSET: Final[int] = 11
HOSTILE_ERROR_MESSAGE: Final[str] = 'boom <script>alert("e")</script> & {{ 7*7 }}'
HOSTILE_DESCRIPTION: Final[str] = "Description & <b>bold</b> {{ 7*7 }}"

#: What a template engine would have produced from ``{{ 7*7 }}``.  Its absence
#: is the assertion.
EVALUATED_EXPRESSION: Final[str] = "49"

# --------------------------------------------------------------------------
# Synthetic result documents
#
# The sample document reaches passed, failed, skipped and undefined steps, a
# Background, an outline and an unselected scenario.  Pending, untested,
# ambiguous and the unknown fallback need documents of their own, and so do the
# empty and hostile cases, so they are built here in the shape
# ``app/reporting/events.py`` owns.
# --------------------------------------------------------------------------

#: The ``type`` of a scenario element, as opposed to a Background occurrence.
SCENARIO_TYPE: Final[str] = "scenario"

#: Schema version of the port's internal result document.
SCHEMA_VERSION: Final[int] = 1


def build_step(
    name: str,
    status: str,
    duration: int | None = None,
    keyword: str = "Given ",
    line: int = 10,
    location: str = "features.steps.synthetic_steps.step",
    error_message: str | None = None,
    arguments: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Build one step node.

    :param name: The step text, already substituted for an outline row.
    :param status: The raw ``result.status``, passed through untouched so a
        value the model never produces can be driven into the pages.
    :param duration: Nanoseconds, or ``None`` to omit the key entirely -- a
        skipped step legitimately carries no duration, and the writer must not
        infer one from the status.
    :param keyword: The Gherkin keyword, trailing space included.
    :param line: The step's line in its feature file.
    :param location: The step implementation, which the steps overview
        aggregates by.
    :param error_message: Failure text, omitted when ``None``.
    :param arguments: ``{"val", "offset"}`` mappings for the matched
        arguments the step row splices into the name.
    :returns: The step node.
    """
    result: dict[str, Any] = {"status": status}
    if duration is not None:
        result["duration"] = duration
    if error_message is not None:
        result["error_message"] = error_message
    match: dict[str, Any] = {"location": location}
    if arguments:
        match["arguments"] = list(arguments)
    return {
        "keyword": keyword,
        "name": name,
        "line": line,
        "match": match,
        "matched": True,
        "result": result,
    }


def build_element(
    name: str,
    steps: tuple[dict[str, Any], ...],
    element_type: str = SCENARIO_TYPE,
    keyword: str = "Scenario",
    line: int = 5,
    element_id: str = "synthetic;scenario",
    selected: bool = True,
    tags: tuple[dict[str, Any], ...] = (),
    start_timestamp: str = SAMPLE_START_TIMESTAMP,
    description: str = "",
) -> dict[str, Any]:
    """Build one Background or scenario element.

    :param name: The element's name.
    :param steps: Its steps, in line order.
    :param element_type: ``"scenario"`` or ``"background"``.
    :param keyword: The Gherkin keyword.
    :param line: The element's line in its feature file.
    :param element_id: The element id, which need not be unique -- two
        elements of this suite share one.
    :param selected: ``False`` for a scenario the tag expression excluded,
        which never ran and must not reach a tag page.
    :param tags: Element-level tags.
    :param start_timestamp: The scenario's start moment, shown on every page
        that shows a scenario.
    :param description: The element's description block.
    :returns: The element node.
    """
    element: dict[str, Any] = {
        "type": element_type,
        "keyword": keyword,
        "name": name,
        "line": line,
        "description": description,
        "selected": selected,
        "steps": list(steps),
    }
    if element_type == SCENARIO_TYPE:
        element["id"] = element_id
        element["start_timestamp"] = start_timestamp
    if tags:
        element["tags"] = list(tags)
    return element


def build_feature(
    filename: str,
    name: str,
    elements: tuple[dict[str, Any], ...],
    tags: tuple[str, ...] = (),
    feature_id: str = "synthetic-feature",
    line: int = 1,
    description: str = "",
) -> dict[str, Any]:
    """Build one feature node whose URI follows the port's own convention.

    :param filename: The feature file's name, e.g. ``"Synthetic.feature"``.
        The URI is ``file:features/<filename>``, which is what the page
        filename hash is computed over.
    :param name: The feature name.
    :param elements: Its Background occurrences and scenarios, in line order.
    :param tags: Feature-level tag names, leading ``@`` included.  They
        propagate onto every scenario of the feature.
    :param feature_id: The feature id, which this suite does not guarantee to
        be unique.
    :param line: The ``Feature:`` line.
    :param description: The feature's description block.
    :returns: The feature node.
    """
    return {
        "uri": f"{FEATURE_URI_PREFIX}{filename}",
        "path": f"features/{filename}",
        "id": feature_id,
        "keyword": "Feature",
        "name": name,
        "line": line,
        "description": description,
        "tags": [
            {"name": tag, "type": "Tag", "location": {"line": 1, "column": 1}}
            for tag in tags
        ],
        "elements": list(elements),
    }


def build_document(*features: dict[str, Any]) -> dict[str, Any]:
    """Wrap ``features`` in a merged result document.

    ``generated_at`` is set so that the build date every page carries is a
    function of the document alone, which is what makes two renders of one
    synthetic document comparable byte for byte.

    :param features: The feature nodes, in source order.
    :returns: The document.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "started_at": "2022-09-07T13:37:26.297Z",
        "generated_at": "2022-09-07T13:39:12.484Z",
        "dry_run": False,
        "tag_expression": "not @wip",
        "metadata": {"implementation": {"name": "behave", "version": "1.3.3"}},
        "features": list(features),
    }


def document_with_features(document: Any, *filenames: str) -> dict[str, Any]:
    """Return ``document`` reduced to the features named by ``filenames``.

    Used for the second-render case: the same document with fewer features is
    exactly the input that leaves an unpruned tree holding surplus pages.

    :param document: A merged result document.
    :param filenames: Feature file names to keep, e.g. ``"Crm.feature"``.
    :returns: A shallow copy carrying only those features, in source order.
    :raises AssertionError: If a named feature is not in the document, which
        would silently weaken every assertion made against the result.
    """
    wanted = {f"{FEATURE_URI_PREFIX}{filename}" for filename in filenames}
    kept = [feature for feature in document["features"] if feature["uri"] in wanted]
    assert {feature["uri"] for feature in kept} == wanted
    reduced = dict(document)
    reduced["features"] = kept
    return reduced


# --------------------------------------------------------------------------
# Parsing
#
# One parser, used by every structural assertion below.  It collects what an
# artifact contract is about: the elements and their attributes, every href and
# src, the status badges with their labels, the document title and the text a
# reader sees, with script and style content excluded from the text so that a
# vendored library's own string cannot satisfy a content assertion.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Reference:
    """One ``href`` or ``src`` occurrence.

    :ivar tag: The element carrying it, e.g. ``"link"``.
    :ivar attribute: ``"href"`` or ``"src"``.
    :ivar value: The attribute's raw value, with character references already
        resolved by the parser.
    """

    tag: str
    attribute: str
    value: str


@dataclass(frozen=True)
class Badge:
    """One status badge, as ``partials/status_badge.html`` emits it.

    :ivar status: The value of whichever hook attribute the badge carries, or
        ``""`` when it carries neither spelling.
    :ivar label: The badge's text content, which is its accessible name.
    :ivar classes: Its class tokens.
    """

    status: str
    label: str
    classes: tuple[str, ...]


class _PageCollector(HTMLParser):
    """Collect the structure of one page.

    Deliberately a collector rather than a validator: it gathers elements,
    references, badges, titles and visible text in document order and makes no
    judgement, so every judgement in this module is made by an assertion that
    names its own reason.
    """

    #: Elements whose character data is markup for a machine rather than text
    #: for a reader.  Excluded from :attr:`text`.
    _OPAQUE_TAGS: Final[frozenset[str]] = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.references: list[Reference] = []
        self.badges: list[Badge] = []
        self.titles: list[str] = []
        self.declarations: list[str] = []
        self._text: list[str] = []
        self._opaque_depth = 0
        self._title_parts: list[str] | None = None
        # One frame per open <span>, carrying the badge under construction or
        # None for a span that is not a badge, so nesting cannot mis-close one.
        self._span_frames: list[tuple[str, tuple[str, ...], list[str]] | None] = []

    def handle_decl(self, decl: str) -> None:
        """Record a markup declaration, which is how the doctype arrives."""
        self.declarations.append(decl)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record one element, its attributes and any reference it carries."""
        attributes = {name: (value if value is not None else "") for name, value in attrs}
        self.elements.append((tag, attributes))
        for attribute in ("href", "src"):
            if attribute in attributes:
                self.references.append(
                    Reference(tag=tag, attribute=attribute, value=attributes[attribute])
                )
        if tag in self._OPAQUE_TAGS:
            self._opaque_depth += 1
        if tag == "title":
            self._title_parts = []
        if tag == "span":
            classes = tuple(attributes.get("class", "").split())
            if BADGE_CLASS in classes:
                status = ""
                for hook in STATUS_HOOK_ATTRIBUTES:
                    if hook in attributes:
                        status = attributes[hook]
                        break
                self._span_frames.append((status, classes, []))
            else:
                self._span_frames.append(None)

    def handle_endtag(self, tag: str) -> None:
        """Close whichever structure ``tag`` opened."""
        if tag in self._OPAQUE_TAGS and self._opaque_depth > 0:
            self._opaque_depth -= 1
        if tag == "title" and self._title_parts is not None:
            self.titles.append("".join(self._title_parts))
            self._title_parts = None
        if tag == "span" and self._span_frames:
            frame = self._span_frames.pop()
            if frame is not None:
                status, classes, parts = frame
                self.badges.append(
                    Badge(status=status, label="".join(parts).strip(), classes=classes)
                )

    def handle_data(self, data: str) -> None:
        """Route character data to the structures currently open."""
        if self._title_parts is not None:
            self._title_parts.append(data)
        if self._opaque_depth:
            return
        self._text.append(data)
        for frame in self._span_frames:
            if frame is not None:
                frame[2].append(data)

    @property
    def text(self) -> str:
        """The page's visible text, with script and style content excluded."""
        return "".join(self._text)


@dataclass(frozen=True)
class ParsedPage:
    """One parsed page, as the assertions below read it.

    :ivar name: The page's filename.
    :ivar html: Its exact markup, for the assertions that are about the bytes
        rather than about the DOM -- escaping and conflict markers.
    :ivar elements: Every element as ``(tag, attributes)``, in document order.
    :ivar references: Every ``href`` and ``src``.
    :ivar badges: Every status badge.
    :ivar titles: Every ``<title>`` text, so "exactly one" is assertable.
    :ivar declarations: Markup declarations, the doctype among them.
    :ivar text: The visible text, with character references resolved, so a
        value can be asserted in its original spelling rather than escaped.
    """

    name: str
    html: str
    elements: tuple[tuple[str, dict[str, str]], ...]
    references: tuple[Reference, ...]
    badges: tuple[Badge, ...]
    titles: tuple[str, ...]
    declarations: tuple[str, ...]
    text: str

    @property
    def title(self) -> str:
        """The page's single title.

        :returns: The title text.
        :raises AssertionError: If the page carries none or more than one, in
            which case no assertion about "the" title would mean anything.
        """
        assert len(self.titles) == 1, f"{self.name} carries {len(self.titles)} titles"
        return self.titles[0]

    @property
    def reference_values(self) -> tuple[str, ...]:
        """Every reference value, in document order and with duplicates kept."""
        return tuple(reference.value for reference in self.references)

    @property
    def normalized_text(self) -> str:
        """The visible text with runs of whitespace collapsed to one space.

        Templates break lines and indent for legibility, so a value that
        occupies one line of the result model may occupy three of the page.
        Collapsing makes a content assertion about the value rather than about
        the template's line breaks.
        """
        return re.sub(r"\s+", " ", self.text)

    def badge_labels(self) -> frozenset[str]:
        """The distinct badge labels the page carries."""
        return frozenset(badge.label for badge in self.badges)


def parse_page(name: str, html: str) -> ParsedPage:
    """Parse one page into a :class:`ParsedPage`.

    :param name: The page's filename, used in assertion messages.
    :param html: Its markup.
    :returns: The parsed page.
    """
    collector = _PageCollector()
    collector.feed(html)
    collector.close()
    return ParsedPage(
        name=name,
        html=html,
        elements=tuple(collector.elements),
        references=tuple(collector.references),
        badges=tuple(collector.badges),
        titles=tuple(collector.titles),
        declarations=tuple(collector.declarations),
        text=collector.text,
    )


def parse_pages(pages: dict[str, str]) -> dict[str, ParsedPage]:
    """Parse every page of a render, keyed by filename."""
    return {name: parse_page(name, html) for name, html in pages.items()}


def read_tree_pages(root: Path) -> dict[str, ParsedPage]:
    """Read and parse every ``*.html`` file directly inside ``root``.

    :param root: The emitted tree's root.
    :returns: Filename to parsed page.
    """
    return {
        path.name: parse_page(path.name, path.read_text(encoding="utf-8"))
        for path in sorted(root.glob(f"*{PAGE_SUFFIX}"))
        if path.is_file()
    }


# --------------------------------------------------------------------------
# Filesystem helpers
# --------------------------------------------------------------------------


def tree_files(root: Path) -> frozenset[str]:
    """Every file in the tree, as forward-slash paths relative to ``root``."""
    return frozenset(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    )


def asset_files(root: Path) -> frozenset[str]:
    """Every file in the tree that is not a page, i.e. the asset census."""
    return frozenset(
        name for name in tree_files(root) if not name.endswith(PAGE_SUFFIX)
    )


def page_files(root: Path) -> frozenset[str]:
    """Every page in the tree, by filename."""
    return frozenset(
        name for name in tree_files(root) if name.endswith(PAGE_SUFFIX)
    )


def tree_digests(root: Path) -> dict[str, str]:
    """Map every file in the tree to the SHA-256 of its bytes.

    The comparison used wherever "unchanged" or "identical" is the claim: it
    covers content and the file set at once, and names the file that differs.
    """
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def listed_names(directory: Path) -> frozenset[str]:
    """The names ``directory`` actually holds, read from the directory itself.

    ``Path.is_file`` answers through the filesystem's own name resolution,
    which is case-insensitive on some of them, so a page linking
    ``js/chart.min.js`` could appear to resolve against ``js/Chart.min.js``.
    A directory listing reports the real spelling and cannot be fooled that
    way, which is why the case assertions go through this.

    :param directory: The directory to list.
    :returns: Its entry names.
    """
    return frozenset(os.listdir(directory))


def split_reference(value: str) -> str:
    """Return the path part of a reference, without its query or fragment.

    The vendored stylesheets request fonts as
    ``../fonts/fontawesome-webfont.eot?v=4.6.3`` and
    ``../fonts/fontawesome-webfont.svg?v=4.6.3#fontawesomeregular``: the query
    and the fragment are the font format's own, and the file on disk carries
    neither.

    :param value: A reference value.
    :returns: Just the path.
    """
    return value.split("#", 1)[0].split("?", 1)[0]


def is_in_page(value: str) -> bool:
    """Answer whether ``value`` names a position in the page rather than a file."""
    return value.startswith("#")


def is_data_uri(value: str) -> bool:
    """Answer whether ``value`` carries its own payload."""
    return value.startswith(DATA_URI_PREFIX)


def is_external(value: str) -> bool:
    """Answer whether ``value`` would take a reader off the tree.

    An absolute, ``/``-rooted reference counts: opened over ``file:``, it
    resolves against the filesystem root rather than against the tree.
    """
    return value.startswith(EXTERNAL_REFERENCE_PREFIXES) or value.startswith("/")


def resolvable_references(page: ParsedPage) -> tuple[str, ...]:
    """The references of ``page`` that must resolve to a file in the tree."""
    return tuple(
        reference.value
        for reference in page.references
        if not is_in_page(reference.value) and not is_data_uri(reference.value)
    )


def css_url_references(text: str) -> tuple[str, ...]:
    """Every ``url(...)`` target declared in a stylesheet.

    :param text: The stylesheet's text.
    :returns: The raw targets, quotes stripped, in declaration order.
    """
    return tuple(
        match.group(1).strip("'\"")
        for match in re.finditer(r"url\(\s*([^)]+?)\s*\)", text)
    )


def jvm_string_hash(text: str) -> int:
    """An independent ``java.lang.String.hashCode`` for cross-checking.

    Deliberately not the writer's implementation and not a copy of it: the
    UTF-16 code units are obtained by *encoding* the string, where the writer
    derives them with surrogate arithmetic, and the 32-bit truncation is done
    once at the end with a modulus where the writer masks on every iteration.
    Two implementations that agree on every input this suite carries is
    evidence; one implementation compared against itself is not.

    :param text: The string to hash.
    :returns: The hash in ``[-2147483648, 2147483647]``.
    """
    raw = text.encode("utf-16-be")
    accumulator = 0
    for index in range(0, len(raw), 2):
        unit = int.from_bytes(raw[index : index + 2], "big")
        accumulator = accumulator * 31 + unit
    accumulator %= 2**32
    return accumulator - 2**32 if accumulator >= 2**31 else accumulator


# --------------------------------------------------------------------------
# Fixtures
#
# One shared template environment.  A render compiles nine templates from
# disk, and an environment caches them, so building one per module rather than
# one per render keeps this suite fast without changing a single byte of
# output: the writer documents the injected environment as the repeat-render
# path, and the environment it builds for itself is the same loader over the
# same root with the same autoescape policy.  One test deliberately takes the
# default path instead -- ``test_whole_tree_writes_from_an_unrelated_working_
# directory``, where ``build_environment`` resolving the package-relative
# template root is the property under test.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pretty_env() -> Environment:
    """The writer's own template environment, built once for this module."""
    return pretty_reports.build_environment()


@pytest.fixture
def empty_template_env(tmp_path: Path) -> Environment:
    """An environment whose loader root holds no template at all.

    The deterministic render fault: the first ``get_template`` raises
    :class:`jinja2.TemplateNotFound`, which is a genuine render error rather
    than a test outcome, and the writer raises it before opening any file.

    :param tmp_path: pytest's per-test temporary directory.
    :returns: An environment that can render nothing.
    """
    root = tmp_path / "no-templates"
    root.mkdir()
    return Environment(loader=FileSystemLoader(str(root)), autoescape=True)


@pytest.fixture
def sample_tree(
    sample_result_set: Any, tmp_artifact_root: Path, pretty_env: Environment
) -> Path:
    """The sample document written as a complete tree, and its root.

    The starting point of every test that reads the artifact from disk.  The
    build date is not pinned on purpose: the sample document carries
    ``generated_at``, so the writer's own :func:`format_build_date` is already
    deterministic over it, and a test that pinned the date would not notice if
    that stopped being true.

    :param sample_result_set: The parsed sample document.
    :param tmp_artifact_root: An empty checkout root, with no ``target/``.
    :param pretty_env: The shared template environment.
    :returns: The directory the writer wrote.
    """
    return pretty_reports.write_pretty_reports(
        sample_result_set, base=tmp_artifact_root, environment=pretty_env
    )


# --------------------------------------------------------------------------
# The filename hash
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "signed", "segment"), HASH_EDGE_CASES)
def test_java_hash_code_reproduces_the_jvm_edge_cases(
    text: str, signed: int, segment: str
) -> None:
    """The three inputs that pin the arithmetic rather than the plumbing.

    An empty string hashes to zero as in Java; ``@Smoke`` hashes to the value
    the reference tree's own tag filename was built from; and
    ``polygenelubricants`` hashes to ``Integer.MIN_VALUE``, the one input whose
    offset filename segment is negative, which an implementation masking to 32
    unsigned bits would render as ``4294967295``.
    """
    assert pretty_reports.java_hash_code(text) == signed
    assert pretty_reports.to_valid_file_name(text) == segment
    assert int(segment) == signed + INT32_MAX


def test_java_hash_code_agrees_with_an_independent_implementation() -> None:
    """Every hash input this suite uses, checked against a second algorithm.

    :func:`jvm_string_hash` derives the UTF-16 code units by encoding and
    truncates once at the end; the writer derives them with surrogate
    arithmetic and masks on every iteration.  Agreement across the ten feature
    URIs, the tags, the edge cases and a non-BMP string is evidence that the
    filename rule is the JVM's and not merely self-consistent.
    """
    inputs = [
        *(f"{FEATURE_URI_PREFIX}{filename}" for filename in SUITE_FEATURE_PAGES),
        SMOKE_TAG,
        UNSELECTED_TAG,
        HOSTILE_TAG,
        "",
        "polygenelubricants",
        # Outside the Basic Multilingual Plane: one code point, two UTF-16
        # code units, and therefore two terms in a Java hash.
        "tag-\U0001f600",
        "Ankara \u0130stanbul",
    ]
    for text in inputs:
        assert pretty_reports.java_hash_code(text) == jvm_string_hash(text), text


def test_java_hash_code_rejects_a_non_string_input() -> None:
    """A non-string is a call-site error, and coercing it would invent a name.

    The writer raises rather than coercing, because a coerced value produces a
    filename no test could have predicted and no page could have linked.
    """
    with pytest.raises(TypeError):
        pretty_reports.java_hash_code(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        pretty_reports.to_valid_file_name(7)  # type: ignore[arg-type]


@pytest.mark.parametrize(("filename", "page"), sorted(SUITE_FEATURE_PAGES.items()))
def test_feature_page_name_is_the_pinned_hash_for_every_suite_feature(
    filename: str, page: str
) -> None:
    """All ten feature files, each asserted against its exact page filename.

    AAP 0.3.4 requires exact filenames rather than merely numeric-looking
    ones, and requires all ten because a full run emits all ten.  The hash
    input is the complete ``file:``-prefixed URI, which is why these values
    differ from the committed reference tree's: AAP deviation 1 moved the
    feature directory and deviation 18 records the consequence.
    """
    assert pretty_reports.feature_page_name(f"{FEATURE_URI_PREFIX}{filename}") == page


def test_every_suite_feature_file_exists_and_is_accounted_for() -> None:
    """The ten pinned page names cover exactly the feature files on disk.

    Without this, a feature file added to the suite would quietly acquire no
    pinned filename at all, and the parametrized test above would still pass.
    The directory comes from ``app.utils.paths`` rather than from a literal.
    """
    on_disk = {path.name for path in paths.features_dir().glob("*.feature")}
    assert on_disk == set(SUITE_FEATURE_PAGES)


def test_tag_page_name_is_the_pinned_hash_for_the_smoke_tag() -> None:
    """``@Smoke`` keeps the reference tree's own filename.

    The hash input includes the leading ``@``, and tag text did not move with
    the feature directory, so this one filename is unchanged from the
    committed reference: ``report-tag_4059758862.html`` there and here.
    """
    assert pretty_reports.tag_page_name(SMOKE_TAG) == SMOKE_TAG_PAGE
    assert pretty_reports.tag_page_name(SMOKE_TAG) != pretty_reports.feature_page_name(
        f"{FEATURE_URI_PREFIX}Crm.feature"
    )


def test_page_names_carry_the_contract_prefixes_and_suffix() -> None:
    """Every pinned name is prefix, unsigned decimal hash and ``.html``."""
    for page in SUITE_FEATURE_PAGES.values():
        assert page.startswith(FEATURE_PAGE_PREFIX) and page.endswith(PAGE_SUFFIX)
        assert page[len(FEATURE_PAGE_PREFIX) : -len(PAGE_SUFFIX)].isdigit()
    assert SMOKE_TAG_PAGE.startswith(TAG_PAGE_PREFIX)
    assert SMOKE_TAG_PAGE[len(TAG_PAGE_PREFIX) : -len(PAGE_SUFFIX)].isdigit()


def test_feature_page_name_is_computed_from_the_uri_not_the_id() -> None:
    """Two features sharing an id still get two pages.

    Contact and Inventory share a title and therefore the ``id`` derived from
    it, as do Login and Notes.  The hash is over the URI, so the collision
    costs nothing: four distinct URIs, four distinct pages.
    """
    colliding = ("Contact.feature", "Inventory.feature", "Login.feature", "Notes.feature")
    pages = {
        pretty_reports.feature_page_name(f"{FEATURE_URI_PREFIX}{name}")
        for name in colliding
    }
    assert len(pages) == len(colliding)
    assert pages == {SUITE_FEATURE_PAGES[name] for name in colliding}


def test_feature_href_map_drops_the_ambiguous_id_and_name_keys(
    sample_result_set: Any,
) -> None:
    """A key resolving to two pages is removed rather than guessed at.

    Contact and Inventory share both ``id`` and ``name`` in the sample
    document, so keeping either key would link one feature's failure to the
    other feature's page.  ``uri`` and ``path`` are unique and stay, which is
    what every consumer tries first.
    """
    features = pretty_reports.emitted_features(sample_result_set)
    hrefs = pretty_reports.feature_href_map(features)

    for uri, page in SAMPLE_FEATURE_PAGES:
        assert hrefs[uri] == page
        assert hrefs[uri.removeprefix("file:")] == page
    assert SHARED_FEATURE_ID not in hrefs
    assert "Testinium app Inventory feature" not in hrefs
    assert hrefs["Testinium app CRM Module"] == SUITE_FEATURE_PAGES["Crm.feature"]


# --------------------------------------------------------------------------
# The page set: cardinality, order and placement
# --------------------------------------------------------------------------


def test_render_pretty_pages_emits_the_exact_page_set_in_write_order(
    sample_result_set: Any, pretty_env: Environment
) -> None:
    """Four overviews, then a page per feature in source order, then the tags.

    The order is asserted as a sequence rather than as a set: it is the order
    the writer writes the files in, and the source order of the feature pages
    is what keeps a rerun over one input from reshuffling the tree.
    """
    pages = pretty_reports.render_pretty_pages(
        sample_result_set, environment=pretty_env
    )

    assert tuple(pages) == SAMPLE_PAGES
    assert len(pages) == len(OVERVIEW_PAGES) + len(SAMPLE_FEATURE_PAGES) + 1


def test_render_pretty_pages_emits_one_detail_page_per_feature_and_tag(
    sample_result_set: Any, pretty_env: Environment
) -> None:
    """The cardinality rule, stated as counts rather than as a page list."""
    pages = pretty_reports.render_pretty_pages(
        sample_result_set, environment=pretty_env
    )
    features = pretty_reports.emitted_features(sample_result_set)
    tags = pretty_reports.collect_tags(features)

    feature_pages = [name for name in pages if name.startswith(FEATURE_PAGE_PREFIX)]
    tag_pages = [name for name in pages if name.startswith(TAG_PAGE_PREFIX)]

    assert len(features) == 4
    assert len(feature_pages) == len(features)
    assert len(tag_pages) == len(tags) == 1
    assert feature_pages == [
        pretty_reports.feature_page_name(feature["uri"]) for feature in features
    ]
    assert tag_pages == [pretty_reports.tag_page_name(tag) for tag in tags]


def test_a_tag_carried_only_by_an_unselected_scenario_gets_no_page(
    sample_result_set: Any, pretty_env: Environment
) -> None:
    """``@wip`` is in the document and must not be in the tree.

    The sample's Sales feature carries one scenario with ``"selected": false``
    tagged ``@wip``.  It never ran, so the JVM emitted no tag object for it:
    no row, no page.
    """
    features = pretty_reports.emitted_features(sample_result_set)
    tags = pretty_reports.collect_tags(features)
    pages = pretty_reports.render_pretty_pages(
        sample_result_set, environment=pretty_env
    )

    assert UNSELECTED_TAG not in tags
    assert pretty_reports.tag_page_name(UNSELECTED_TAG) not in pages
    assert set(tags) == {SMOKE_TAG}


def test_render_pretty_pages_of_nothing_emits_only_the_four_overviews(
    pretty_env: Environment,
) -> None:
    """A run that produced nothing still produces all four overview pages.

    The exit contract requires the artifacts even when the tag expression
    selects nothing, so ``None`` is a complete four-page render rather than an
    error or an empty result.
    """
    pages = pretty_reports.render_pretty_pages(None, environment=pretty_env)

    assert tuple(pages) == OVERVIEW_PAGES
    for name, html in pages.items():
        page = parse_page(name, html)
        assert page.declarations == ("DOCTYPE html",)
        assert page.title


@pytest.mark.parametrize(
    "document",
    [
        pytest.param({}, id="no-features-key"),
        pytest.param({"features": []}, id="empty-feature-list"),
        pytest.param(
            build_document(
                build_feature(
                    "Unselected.feature",
                    "Nothing ran",
                    (
                        build_element(
                            "excluded",
                            (build_step("a step", "skipped", duration=0),),
                            selected=False,
                        ),
                    ),
                )
            ),
            id="every-element-unselected",
        ),
    ],
)
def test_documents_with_nothing_emitted_yield_only_the_four_overviews(
    document: Any, pretty_env: Environment
) -> None:
    """Three ways of selecting nothing, all four overview pages regardless.

    A feature whose every element was excluded never ran, so it is omitted
    altogether rather than rendered as a row of zeros.
    """
    pages = pretty_reports.render_pretty_pages(document, environment=pretty_env)

    assert tuple(pages) == OVERVIEW_PAGES


def test_write_pretty_reports_returns_the_paths_authority_directory(
    sample_result_set: Any, tmp_artifact_root: Path, pretty_env: Environment
) -> None:
    """The tree lands where ``app.utils.paths`` says, and nowhere else.

    Asserted through the accessors rather than against a path literal, and
    then decomposed so the ``cucumber-html-reports`` placement AAP 0.3.4
    specifies -- one level below ``target/cucumber`` -- is itself the claim.
    """
    root = pretty_reports.write_pretty_reports(
        sample_result_set, base=tmp_artifact_root, environment=pretty_env
    )

    assert root == paths.pretty_reports_html_dir(tmp_artifact_root)
    assert root.is_dir()
    assert root.name == TREE_DIR_NAME
    assert root.parent == paths.pretty_reports_dir(tmp_artifact_root)
    assert root.parent.name == TREE_PARENT_DIR_NAME
    assert root.parent.parent == paths.target_root(tmp_artifact_root)
    assert root.parent.parent.name == BUILD_OUTPUT_DIR_NAME
    assert root.parent.parent.parent == tmp_artifact_root


def test_write_pretty_reports_creates_the_build_output_directory_itself(
    sample_result_set: Any, tmp_artifact_root: Path, pretty_env: Environment
) -> None:
    """``target/`` is absent beforehand: the writer creates its own path."""
    assert not paths.target_root(tmp_artifact_root).exists()

    root = pretty_reports.write_pretty_reports(
        sample_result_set, base=tmp_artifact_root, environment=pretty_env
    )

    assert root.is_dir()
    assert paths.pretty_reports_index_path(tmp_artifact_root).is_file()


def test_write_pretty_reports_works_into_an_existing_build_directory(
    sample_result_set: Any, prepared_artifact_root: Path, pretty_env: Environment
) -> None:
    """``target/`` already present is the ordinary case, not the exception.

    ``--clean`` empties the build-output directory before a run, so the writer
    normally finds it there and has to write into it rather than insisting on
    creating it.  Both paths therefore produce the same tree.
    """
    assert paths.target_root(prepared_artifact_root).is_dir()

    root = pretty_reports.write_pretty_reports(
        sample_result_set, base=prepared_artifact_root, environment=pretty_env
    )

    assert root == paths.pretty_reports_html_dir(prepared_artifact_root)
    assert page_files(root) == frozenset(SAMPLE_PAGES)
    assert asset_files(root) == EXPECTED_ASSETS


def test_write_pretty_reports_honours_an_explicit_directory(
    sample_result_set: Any, tmp_path: Path, pretty_env: Environment
) -> None:
    """``directory=`` overrides ``base=`` entirely, for a caller holding a path."""
    explicit = tmp_path / "explicit-tree"

    root = pretty_reports.write_pretty_reports(
        sample_result_set, directory=explicit, environment=pretty_env
    )

    assert root == explicit
    assert page_files(root) == frozenset(SAMPLE_PAGES)


def test_written_tree_holds_exactly_the_rendered_pages_byte_for_byte(
    sample_result_set: Any, sample_tree: Path, pretty_env: Environment
) -> None:
    """What is on disk is what the pure renderer produced, page for page.

    This is the seam between the two halves of the writer: if the written
    bytes and a fresh render of the same document ever diverge, every
    structural assertion made against either one stops being about the other.
    """
    rendered = pretty_reports.render_pretty_pages(
        sample_result_set, environment=pretty_env
    )

    assert page_files(sample_tree) == frozenset(rendered)
    for name, html in rendered.items():
        assert (sample_tree / name).read_text(encoding="utf-8") == html


def test_overview_index_is_the_features_overview(sample_tree: Path) -> None:
    """The page served when a request names the artifact directory itself."""
    index = sample_tree / OVERVIEW_INDEX

    assert index.is_file()
    assert index.name == paths.PRETTY_OVERVIEW_INDEX
    assert OVERVIEW_PAGES[0] == OVERVIEW_INDEX


def test_empty_document_writes_four_pages_and_the_whole_asset_set(
    tmp_artifact_root: Path, pretty_env: Environment
) -> None:
    """A run that produced nothing still ships a complete, openable tree.

    Four pages, no detail page, and all 22 assets: the overview pages link the
    same eleven files as any other page, so a shortened asset copy would ship
    four broken pages rather than four empty ones.
    """
    root = pretty_reports.write_pretty_reports(
        None, base=tmp_artifact_root, environment=pretty_env
    )

    assert page_files(root) == frozenset(OVERVIEW_PAGES)
    assert asset_files(root) == EXPECTED_ASSETS
    assert not [name for name in page_files(root) if name.startswith("report-")]


# --------------------------------------------------------------------------
# The asset census
# --------------------------------------------------------------------------


def test_asset_census_is_exactly_the_twenty_two_expected_files(
    sample_tree: Path,
) -> None:
    """The emitted asset set equals the expected set: no more, no fewer.

    Compared as one set against a literal, so a file that stopped being
    copied and a file that started being copied both fail here.  The group
    sizes are asserted alongside it, which is what turns a diff into a
    sentence: three vendored stylesheets, five vendored scripts, eleven fonts,
    the favicon, and the port's own two.
    """
    emitted = asset_files(sample_tree)

    assert emitted == EXPECTED_ASSETS
    assert len(emitted) == 22
    assert len(VENDORED_CSS_ASSETS) == 3
    assert len(VENDORED_JS_ASSETS) == 5
    assert len(VENDORED_FONT_ASSETS) == 11
    assert len(VENDORED_IMAGE_ASSETS) == 1
    assert len(VENDORED_ASSETS) == 20
    assert len(PORT_ASSETS) == 2


def test_asset_census_covers_every_asset_the_pages_link(
    sample_tree: Path,
) -> None:
    """Every file the layout links by name is in the census and on disk.

    A page linking a file the copy did not write is a broken artifact rather
    than a cosmetic problem, so this is asserted from both ends: the expected
    census contains the linked set, and each linked file is a real file.
    """
    assert PAGE_LINKED_ASSETS <= EXPECTED_ASSETS
    assert frozenset(pretty_reports.PAGE_LINKED_ASSETS) == PAGE_LINKED_ASSETS
    assert frozenset(pretty_reports.VENDORED_FONT_ASSETS) == frozenset(
        VENDORED_FONT_ASSETS
    )
    for name in PAGE_LINKED_ASSETS:
        assert sample_tree.joinpath(*name.split("/")).is_file(), name


def test_asset_names_are_case_exact_in_the_directory_listing(
    sample_tree: Path,
) -> None:
    """``Chart.min.js`` keeps its capital C, read from the directory itself.

    ``Path.is_file`` asks the filesystem to resolve a name, and some
    filesystems resolve case-insensitively, so a page linking
    ``js/chart.min.js`` could appear to work on a developer's machine and 404
    on every Linux build agent.  A directory listing reports the real
    spelling, so the comparison is made against that.
    """
    for subdirectory in ASSET_SUBDIRECTORIES:
        expected = {
            name.split("/", 1)[1]
            for name in EXPECTED_ASSETS
            if name.startswith(f"{subdirectory}/")
        }
        assert listed_names(sample_tree / subdirectory) == expected, subdirectory

    assert "Chart.min.js" in listed_names(sample_tree / "js")
    assert "chart.min.js" not in listed_names(sample_tree / "js")
    assert "FontAwesome.otf" in listed_names(sample_tree / "fonts")


def test_tree_holds_only_the_four_asset_subdirectories(sample_tree: Path) -> None:
    """The tree's shape: four asset directories and the pages, nothing else."""
    directories = {path.name for path in sample_tree.iterdir() if path.is_dir()}
    assert directories == set(ASSET_SUBDIRECTORIES)
    assert frozenset(pretty_reports.ASSET_SUBDIRECTORIES) == frozenset(
        ASSET_SUBDIRECTORIES
    )


@pytest.mark.parametrize("name", sorted(VENDORED_ASSETS))
def test_each_vendored_asset_is_copied_byte_for_byte(
    name: str, sample_tree: Path
) -> None:
    """The copy preserves bytes, for all twenty vendored files.

    These are the reference generator's own minified libraries and icon fonts
    and a report contract in their own right: they are copied, never
    regenerated, re-minified or re-encoded.  Byte equality against
    ``app/static/vendor`` is the whole claim, and it is what makes the
    provenance recorded for those files mean anything after the copy.
    """
    source = paths.vendor_dir().joinpath(*name.split("/"))
    copied = sample_tree.joinpath(*name.split("/"))

    assert source.is_file(), source
    assert copied.read_bytes() == source.read_bytes()


@pytest.mark.parametrize(("source_name", "destination"), PORT_ASSETS)
def test_each_port_asset_is_copied_byte_for_byte(
    source_name: str, destination: str, sample_tree: Path
) -> None:
    """``css/main.css`` and ``js/report.js`` travel from ``app/static``.

    The layout links both tree-relative in addition to the vendored files, so
    omitting either would ship every page with two dead references.
    """
    source = paths.static_dir().joinpath(*source_name.split("/"))
    copied = sample_tree.joinpath(*destination.split("/"))

    assert source.is_file(), source
    assert copied.read_bytes() == source.read_bytes()


def test_copy_pretty_assets_returns_every_path_it_wrote(tmp_path: Path) -> None:
    """The return value is the census, as absolute paths, sorted.

    A caller -- the writer's own log line, and this suite -- has to be able to
    assert on the set rather than re-walk the directory to discover it.
    """
    destination = tmp_path / "assets-only"

    written = pretty_reports.copy_pretty_assets(destination)

    assert isinstance(written, tuple)
    assert len(written) == 22
    assert list(written) == sorted(written)
    assert all(path.is_absolute() for path in written)
    assert {
        path.relative_to(destination).as_posix() for path in written
    } == EXPECTED_ASSETS
    assert asset_files(destination) == EXPECTED_ASSETS


def test_copy_pretty_assets_creates_its_destination(tmp_path: Path) -> None:
    """A destination that does not exist yet is created, parents included."""
    destination = tmp_path / "absent" / "deeper" / "tree"
    assert not destination.exists()

    pretty_reports.copy_pretty_assets(destination)

    assert asset_files(destination) == EXPECTED_ASSETS


def test_copy_pretty_assets_is_idempotent_and_still_byte_exact(
    tmp_path: Path,
) -> None:
    """A second copy over the first leaves the same 22 files, same bytes.

    The writer overwrites its tree in place, so the asset copy runs again on
    every render: it has to be repeatable without accumulating, truncating or
    re-encoding anything.
    """
    destination = tmp_path / "twice"

    first = pretty_reports.copy_pretty_assets(destination)
    before = tree_digests(destination)
    second = pretty_reports.copy_pretty_assets(destination)
    after = tree_digests(destination)

    assert first == second
    assert before == after
    assert set(before) == set(EXPECTED_ASSETS)


# --------------------------------------------------------------------------
# Package-relative resources: the installed-wheel property
# --------------------------------------------------------------------------


def test_asset_sources_are_package_relative(tmp_path: Path) -> None:
    """The asset sources live inside the package, not beside the checkout.

    ``pyproject.toml`` declares ``app/templates``, ``app/static`` and
    ``app/static/vendor`` as package data precisely so they travel with an
    installed wheel.  That only helps if the writer reaches them through the
    package-relative accessors, which is what this asserts: both directories
    are inside ``package_root()``, which is derived from the module's own
    ``__file__`` rather than from the working directory.
    """
    package = paths.package_root()

    assert paths.static_dir().is_relative_to(package)
    assert paths.vendor_dir().is_relative_to(package)
    assert paths.templates_dir().is_relative_to(package)
    assert package.is_absolute()
    # The tmp_path is somewhere else entirely, which is the point: nothing
    # about the asset sources is relative to where the process happens to be.
    assert not package.is_relative_to(tmp_path)


def test_assets_copy_correctly_from_an_unrelated_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy works with the process CWD somewhere with no ``app/`` in it.

    This is the installed-wheel case reproduced without installing a wheel: a
    Jenkins agent runs the entry point from a workspace, and an asset source
    resolved against the working directory would be absent there.  The
    destination is passed explicitly so the test is about the *sources*.
    """
    elsewhere = tmp_path / "unrelated-cwd"
    elsewhere.mkdir()
    destination = tmp_path / "from-elsewhere"
    monkeypatch.chdir(elsewhere)

    assert not (Path.cwd() / "app").exists()
    written = pretty_reports.copy_pretty_assets(destination)

    assert len(written) == 22
    assert asset_files(destination) == EXPECTED_ASSETS
    for name in sorted(VENDORED_ASSETS):
        assert destination.joinpath(*name.split("/")).read_bytes() == (
            paths.vendor_dir().joinpath(*name.split("/")).read_bytes()
        )


def test_whole_tree_writes_from_an_unrelated_working_directory(
    sample_result_set: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pages and assets both, with the CWD moved and ``base=`` explicit.

    The template loader root is package-relative too, so this exercises the
    render half of the same property.  The default environment is used
    deliberately -- ``build_environment`` is the code that resolves the
    template root -- rather than the module's shared one.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(elsewhere)

    root = pretty_reports.write_pretty_reports(sample_result_set, base=checkout)

    assert root == paths.pretty_reports_html_dir(checkout)
    assert page_files(root) == frozenset(SAMPLE_PAGES)
    assert asset_files(root) == EXPECTED_ASSETS


# --------------------------------------------------------------------------
# Offline reference resolution
# --------------------------------------------------------------------------


def test_every_href_and_src_resolves_to_a_file_inside_the_tree(
    sample_tree: Path,
) -> None:
    """The artifact's defining property: it opens from a workspace, offline.

    Every reference of every page is resolved against the page's own
    directory, then required to be a real file that is inside the tree.
    In-page fragments and data URIs are excluded because neither names a file;
    both are asserted separately.
    """
    pages = read_tree_pages(sample_tree)
    assert set(pages) == set(SAMPLE_PAGES)

    for name, page in pages.items():
        for value in resolvable_references(page):
            resolved = (sample_tree / split_reference(value)).resolve()
            assert resolved.is_relative_to(sample_tree.resolve()), f"{name}: {value}"
            assert resolved.is_file(), f"{name}: {value}"


def test_no_page_carries_an_external_or_absolute_reference(
    sample_tree: Path,
) -> None:
    """No CDN, no protocol-relative URL, no ``file:`` and no ``/``-rooted path.

    The vendored libraries exist so the pages never reach the network, and an
    absolute path resolves against the filesystem root rather than against the
    tree when the page is opened over ``file:``.
    """
    for name, page in read_tree_pages(sample_tree).items():
        for value in page.reference_values:
            if is_data_uri(value):
                continue
            assert not is_external(value), f"{name}: {value}"
        # The same claim about the raw markup, which covers the inline
        # scripts the references walk above cannot see.
        lowered = page.html.lower()
        for forbidden in ("http://", "https://", '="//', '="file:', "='file:"):
            assert forbidden not in lowered, f"{name}: {forbidden}"


def test_the_only_in_page_reference_is_the_carousel_control(
    sample_tree: Path,
) -> None:
    """Fragments are excluded from the filesystem walk, so they are pinned here.

    The features overview carries the Bootstrap carousel's previous and next
    controls, which target the carousel element in the same page.  No other
    page carries a fragment reference at all, and the target element exists.
    """
    pages = read_tree_pages(sample_tree)
    fragments = {
        name: [value for value in page.reference_values if is_in_page(value)]
        for name, page in pages.items()
    }

    assert set(fragments[OVERVIEW_INDEX]) == IN_PAGE_FRAGMENTS
    assert len(fragments[OVERVIEW_INDEX]) == 2
    for name, values in fragments.items():
        if name != OVERVIEW_INDEX:
            assert values == [], name

    identifiers = {
        attributes["id"]
        for _tag, attributes in pages[OVERVIEW_INDEX].elements
        if "id" in attributes
    }
    for fragment in IN_PAGE_FRAGMENTS:
        assert fragment.removeprefix("#") in identifiers


def test_every_page_links_the_whole_layout_asset_set(sample_tree: Path) -> None:
    """Each page carries all eleven layout references, and no other file.

    A page missing one of them renders without its stylesheet, its sorting
    behaviour or its icon; a page carrying an extra file reference would be
    linking something the census does not guarantee.  Page-to-page links are
    excluded here and asserted in the navigation tests.
    """
    for name, page in read_tree_pages(sample_tree).items():
        linked_assets = {
            value
            for value in resolvable_references(page)
            if not value.endswith(PAGE_SUFFIX)
        }
        assert linked_assets == set(PAGE_LINKED_ASSETS), name


def test_every_page_links_only_pages_that_exist_in_the_tree(
    sample_tree: Path,
) -> None:
    """Page-to-page navigation: four overviews plus real detail pages only."""
    present = page_files(sample_tree)

    for name, page in read_tree_pages(sample_tree).items():
        linked_pages = {
            value
            for value in resolvable_references(page)
            if value.endswith(PAGE_SUFFIX)
        }
        assert linked_pages <= present, name
        assert set(OVERVIEW_PAGES) <= linked_pages, name


def test_vendored_stylesheets_font_requests_resolve_inside_the_tree(
    sample_tree: Path,
) -> None:
    """``url(../fonts/<name>)`` resolves because ``fonts/`` sits beside ``css/``.

    The icon fonts are requested from the copied stylesheets rather than from
    a page, so they are invisible to the ``href``/``src`` walk above and are
    checked here: every ``url()`` target of every copied stylesheet, minus its
    format query and fragment, is a file inside the tree.
    """
    checked = 0
    for name in (*VENDORED_CSS_ASSETS, *(target for _s, target in PORT_ASSETS)):
        if not name.endswith(".css"):
            continue
        stylesheet = sample_tree.joinpath(*name.split("/"))
        text = stylesheet.read_text(encoding="utf-8", errors="replace")
        for value in css_url_references(text):
            if is_data_uri(value):
                continue
            assert not is_external(value), f"{name}: {value}"
            resolved = (stylesheet.parent / split_reference(value)).resolve()
            assert resolved.is_relative_to(sample_tree.resolve()), f"{name}: {value}"
            assert resolved.is_file(), f"{name}: {value}"
            checked += 1

    # Both icon-font families are requested, so the walk above was not vacuous.
    assert checked >= len(VENDORED_FONT_ASSETS)


# --------------------------------------------------------------------------
# Page structure and content
# --------------------------------------------------------------------------


def test_every_page_declares_a_doctype_a_charset_and_one_title(
    sample_tree: Path,
) -> None:
    """The page chrome, on all nine pages of the sample tree.

    One HTML5 doctype, a declared UTF-8 charset -- the result data carries
    quotation marks, apostrophes and characters outside ASCII, so a page
    without one is a page a browser may decode wrongly -- and exactly one
    non-empty title.
    """
    pages = read_tree_pages(sample_tree)
    assert len(pages) == len(SAMPLE_PAGES)

    for name, page in pages.items():
        assert page.declarations == ("DOCTYPE html",), name
        assert page.html.startswith("<!DOCTYPE html>"), name
        assert page.title.strip(), name
        charsets = [
            attributes
            for tag, attributes in page.elements
            if tag == "meta"
            and "utf-8" in f"{attributes.get('charset', '')}"
            f"{attributes.get('content', '')}".lower()
        ]
        assert charsets, name


def test_page_titles_name_their_own_subject(sample_tree: Path) -> None:
    """Each page's title says which page it is, and detail pages say which one.

    Read from the artifact rather than from the templates: the title is what a
    browser tab, a bookmark and the Jenkins publisher's own link text show.
    """
    pages = read_tree_pages(sample_tree)
    expected_fragments = {
        "overview-features.html": "Features Overview",
        "overview-tags.html": "Tags Overview",
        "overview-steps.html": "Steps Overview",
        "overview-failures.html": "Failures Overview",
        SUITE_FEATURE_PAGES["Crm.feature"]: "Feature: Testinium app CRM Module",
        SUITE_FEATURE_PAGES["Sales.feature"]: "Feature: .... app Sales feature",
        SMOKE_TAG_PAGE: f"Tag: {SMOKE_TAG}",
    }

    for name, fragment in expected_fragments.items():
        title = pages[name].title
        assert "Cucumber Reports" in title, name
        assert fragment in title, name


def test_overview_features_lists_every_emitted_feature_and_links_its_page(
    sample_tree: Path,
) -> None:
    """The entry point names all four features and links all four pages.

    Both features that share a title appear, which is what makes the
    duplicate-id case visible in the artifact rather than only in the
    filename rule.
    """
    page = read_tree_pages(sample_tree)[OVERVIEW_INDEX]
    text = page.normalized_text

    for feature_name in SAMPLE_FEATURE_NAMES:
        assert feature_name in text, feature_name
    assert text.count(SAMPLE_FEATURE_NAMES[0]) >= 2

    linked = [
        value for value in page.reference_values if value.startswith(FEATURE_PAGE_PREFIX)
    ]
    assert set(linked) == {page_name for _uri, page_name in SAMPLE_FEATURE_PAGES}


@pytest.mark.parametrize(
    ("filename", "feature_name", "scenario_name", "foreign_scenario"),
    [
        pytest.param(
            "Crm.feature",
            "Testinium app CRM Module",
            "User can create pipeline in the displayed dashboard",
            "Verify that the user can delete a contact from 2 different side",
            id="crm",
        ),
        pytest.param(
            "Contact.feature",
            "Testinium app Inventory feature",
            "Verify that the user can delete a contact from 2 different side",
            "User can create pipeline in the displayed dashboard",
            id="contact",
        ),
        pytest.param(
            "Inventory.feature",
            "Testinium app Inventory feature",
            "Verify that User can reach New Products Form by clicking",
            "Verify that the user can delete a contact from 2 different side",
            id="inventory",
        ),
        pytest.param(
            "Sales.feature",
            ".... app Sales feature",
            "Verify that the user's search finds his name \"Lucas\" from search bar.",
            "User can create pipeline in the displayed dashboard",
            id="sales",
        ),
    ],
)
def test_feature_detail_page_carries_its_own_feature_and_no_other(
    filename: str,
    feature_name: str,
    scenario_name: str,
    foreign_scenario: str,
    sample_tree: Path,
) -> None:
    """A feature page is about one feature: its name, its scenarios, nothing else.

    The ``foreign_scenario`` assertion is what makes the others mean
    something: a page that simply rendered the whole document would satisfy
    every positive check here.
    """
    page = read_tree_pages(sample_tree)[SUITE_FEATURE_PAGES[filename]]
    text = page.normalized_text

    assert feature_name in text
    assert scenario_name in text
    assert foreign_scenario not in text


#: Every selected scenario of the sample document, by the feature file whose
#: page must carry it.  Pinned rather than derived at run time: a set computed
#: from the document through the writer's own selector would shrink along with
#: a page that lost a scenario, and the comparison would still hold.  Read off
#: ``tests/fixtures/sample_results.json`` once.
SAMPLE_PAGE_SCENARIOS: Final[dict[str, tuple[str, ...]]] = {
    "Contact.feature": (
        "Verify that the user can delete a contact from 2 different side",
    ),
    "Crm.feature": (
        "User can create pipeline in the displayed dashboard",
        "User can change the situation in progress",
        "User can change information in dashboard",
    ),
    "Inventory.feature": (
        "Verify that User can reach New Products Form by clicking "
        "Inventory --> Products --> Create",
    ),
    "Sales.feature": (
        'Verify that the user\'s search finds his name "Lucas" from search bar.',
        "Verify that after creating a new customer, the page title includes "
        "the customer name.",
    ),
}

#: Filterable result regions expected on each feature page: every emitted
#: element, Background occurrences included.  CRM's eight is four Background
#: occurrences plus four scenarios.  Pinned for the same reason as the map
#: above -- a count recomputed from the document cannot notice the document
#: losing something.
SAMPLE_PAGE_REGIONS: Final[dict[str, int]] = {
    "Contact.feature": 1,
    "Crm.feature": 8,
    "Inventory.feature": 1,
    "Sales.feature": 3,
}

#: Emitted elements across the whole sample document: 1 + 8 + 1 + 3.
SAMPLE_EMITTED_ELEMENT_TOTAL: Final[int] = 13

#: The hook the shared partials put on a container that is hidden as a unit by
#: the status filter.  Named once here so the cardinality assertion reads as
#: "one result region per element" rather than as a string match.
FILTERABLE_ATTRIBUTE: Final[str] = "data-report-filterable"


def test_every_feature_page_carries_its_whole_scenario_set_and_no_foreign_one(
    sample_tree: Path,
) -> None:
    """Each of the four pages holds all of its own scenarios and none of another's.

    This is the assertion that makes page *cardinality* mean something.  The
    right number of pages with the right names can be emitted while a page's
    body has lost its results, and a spot check of one scenario per page cannot
    see it -- so every selected scenario of every emitted feature is required
    here, on the page whose feature owns it, by a set pinned from the fixture
    rather than recomputed through the writer.

    The negative half is what stops a page that rendered the whole document
    from satisfying the positive half: each page is required to be free of
    every *other* feature's scenarios.  Two of this suite's features share a
    title, so the scenario text is the only thing that tells their pages apart.
    """
    pages = read_tree_pages(sample_tree)
    assert set(SAMPLE_PAGE_SCENARIOS) == {
        uri.removeprefix(FEATURE_URI_PREFIX) for uri, _page in SAMPLE_FEATURE_PAGES
    }, "the pinned scenario map must cover exactly the sample's feature pages"

    for filename, scenarios in SAMPLE_PAGE_SCENARIOS.items():
        page = pages[SUITE_FEATURE_PAGES[filename]]
        text = page.normalized_text
        for scenario in scenarios:
            assert scenario in text, f"{filename}: {scenario!r} is missing"
        foreign = [
            scenario
            for other, names in SAMPLE_PAGE_SCENARIOS.items()
            if other != filename
            for scenario in names
            if scenario not in scenarios
        ]
        for scenario in foreign:
            assert scenario not in text, f"{filename}: carries foreign {scenario!r}"


def test_every_feature_page_holds_one_result_region_per_emitted_element(
    sample_result_set: Any, sample_tree: Path
) -> None:
    """Detail cardinality per page: one filterable result region per element.

    The counts are pinned per page -- Contact one, CRM eight, Inventory one,
    Sales three -- and cross-checked against this module's own reading of the
    selection rule, so neither a page that lost a region nor an oracle that
    drifted satisfies both halves.  CRM's eight is its four Background
    occurrences plus its four scenarios, two of which are the rows of one
    outline: the model records a Background occurrence per scenario and the
    page shows each where the model puts it.

    A region is identified by the pairing the shared partials document -- a
    filterable hook on the container that is hidden as a unit, carrying that
    container's status -- with both status spellings accepted while the
    template owner consolidates them.  What is under test is *how many* result
    regions a page carries, not what they are called.
    """
    derived = {
        filename: sum(
            1
            for feature in sample_result_set["features"]
            if feature["uri"].endswith(f"/{filename}")
            for element in feature["elements"]
            if element.get("selected") is not False
        )
        for filename in SAMPLE_PAGE_REGIONS
    }
    assert derived == dict(SAMPLE_PAGE_REGIONS)
    assert sum(SAMPLE_PAGE_REGIONS.values()) == SAMPLE_EMITTED_ELEMENT_TOTAL

    pages = read_tree_pages(sample_tree)
    for filename, count in SAMPLE_PAGE_REGIONS.items():
        page = pages[SUITE_FEATURE_PAGES[filename]]
        regions = [
            attributes
            for _tag, attributes in page.elements
            if FILTERABLE_ATTRIBUTE in attributes
            and any(
                attributes.get(hook) in STATUS_LABELS
                for hook in STATUS_HOOK_ATTRIBUTES
            )
        ]
        assert len(regions) == count, f"{filename}: {len(regions)} regions, want {count}"


def test_sales_page_omits_the_unselected_scenario(sample_tree: Path) -> None:
    """A scenario the tag expression excluded never ran and is not reported.

    The sample's Sales feature carries four scenarios and one of them is
    unselected, so its page shows three.  Its step text is absent from the
    page altogether.
    """
    page = read_tree_pages(sample_tree)[SUITE_FEATURE_PAGES["Sales.feature"]]
    text = page.normalized_text

    assert "Verify that the user can export the customer list" not in text
    assert "User can export the customer list" not in text
    assert "Verify that the user's search finds his name" in text


def test_overview_tags_carries_the_tag_and_links_its_page(
    sample_tree: Path,
) -> None:
    """One tag, one row, one link, and no row for the unselected-only tag."""
    page = read_tree_pages(sample_tree)[OVERVIEW_PAGES[1]]

    assert SMOKE_TAG in page.normalized_text
    assert UNSELECTED_TAG not in page.normalized_text
    linked = [
        value for value in page.reference_values if value.startswith(TAG_PAGE_PREFIX)
    ]
    assert set(linked) == {SMOKE_TAG_PAGE}


def test_tag_detail_page_lists_exactly_the_tagged_scenarios(
    sample_result_set: Any, sample_tree: Path
) -> None:
    """The tag page shows the four ``@Smoke`` scenarios and links their feature.

    The tag is declared once at the top of ``Crm.feature`` and propagates onto
    every scenario of that feature, which is why all four appear.  A scenario
    from an untagged feature must not.
    """
    page = read_tree_pages(sample_tree)[SMOKE_TAG_PAGE]
    text = page.normalized_text
    features = pretty_reports.emitted_features(sample_result_set)
    subjects = pretty_reports.collect_tags(features)[SMOKE_TAG]

    assert len(subjects) == 4
    for element in subjects:
        assert element["name"] in text, element["name"]
    assert "Verify that the user can delete a contact" not in text
    assert SUITE_FEATURE_PAGES["Crm.feature"] in page.reference_values
    assert "Testinium app CRM Module" in text


def test_overview_failures_carries_both_failure_messages(
    sample_tree: Path,
) -> None:
    """The failures overview is the page a reader opens first when a run fails.

    The subject of each failure is asserted rather than the whole text: AAP
    deviation 16 makes the Python traceback around it non-parity with the
    Java reference, while the assertion's subject and message are parity.
    """
    page = read_tree_pages(sample_tree)[OVERVIEW_PAGES[3]]
    text = page.normalized_text

    for subject in SAMPLE_FAILURE_SUBJECTS:
        assert subject in text, subject
    assert "AssertionError" in text
    assert "NoSuchElementException" in text
    assert "User can change the situation in progress" in text
    # A passing scenario has no place on the failures overview.
    assert "User can create pipeline in the displayed dashboard" not in text


def test_overview_steps_aggregates_every_step_implementation(
    sample_result_set: Any, sample_tree: Path
) -> None:
    """Occurrence counts per implementation, derived from the document itself.

    The expected counts are computed here from the same document the writer
    read, so the assertion covers all seventeen implementations rather than a
    chosen few, and the four pinned pairs stand as the literal values this
    aggregation was verified against.
    """
    page = read_tree_pages(sample_tree)[OVERVIEW_PAGES[2]]
    text = page.normalized_text

    expected: dict[str, int] = {}
    for feature in sample_result_set["features"]:
        for element in feature["elements"]:
            if element.get("selected") is False:
                continue
            for step in element.get("steps", ()):
                location = (step.get("match") or {}).get("location")
                if isinstance(location, str) and location.strip():
                    expected[location] = expected.get(location, 0) + 1

    assert len(expected) == 17
    for location, occurrences in sorted(expected.items()):
        assert f"{location} {occurrences} " in text, location
    for location, occurrences in SAMPLE_STEP_OCCURRENCES:
        assert expected[location] == occurrences
        assert f"{location} {occurrences} " in text

    # An unselected scenario's steps never ran, so they are not aggregated,
    # and a step with no match carries no implementation to aggregate by.
    assert "user_can_export_the_customer_list" not in text
    assert "user_can_search_the_customer_from_the_search_bar" not in text


def test_scenario_start_timestamps_reach_the_pages(sample_tree: Path) -> None:
    """``start_timestamp`` is shown verbatim wherever a scenario is shown."""
    pages = read_tree_pages(sample_tree)

    for name in (SUITE_FEATURE_PAGES["Crm.feature"], SMOKE_TAG_PAGE, OVERVIEW_PAGES[3]):
        assert SAMPLE_START_TIMESTAMP in pages[name].normalized_text, name


def test_durations_render_as_seconds_from_nanosecond_integers(
    sample_tree: Path,
) -> None:
    """Nanosecond integers become ``s.mmm``, with minutes split out above 60s.

    The ``0.000`` value is the load-bearing one: the sample carries a skipped
    step whose duration is exactly ``0``, and a bare nanosecond count or an
    omitted cell would both be wrong there.
    """
    pages = read_tree_pages(sample_tree)
    contact = pages[SUITE_FEATURE_PAGES["Contact.feature"]].normalized_text
    crm = pages[SUITE_FEATURE_PAGES["Crm.feature"]].normalized_text
    tags = pages[OVERVIEW_PAGES[1]].normalized_text
    seconds, zero, minutes, tag_total = SAMPLE_DURATIONS

    assert seconds in contact
    assert minutes in crm
    assert zero in crm
    assert tag_total in tags
    # Never the raw nanosecond integers the model carries.
    assert "2415000000" not in contact
    assert "4211000000" not in crm


def test_the_screenshot_embedding_is_inlined_as_a_png_data_uri(
    sample_result_set: Any, sample_tree: Path
) -> None:
    """The failure screenshot travels inside the page, not beside it.

    The sample document carries one PNG embedding on an after-hook.  It
    appears as an ``img`` whose ``src`` is a ``data:image/png;base64,`` URI
    carrying exactly the fixture's own payload, on the three pages that show
    the failing scenario -- its feature page, its tag page and the failures
    overview -- and on no other page.  Decoding it and checking the PNG
    signature is what distinguishes an inlined image from a truncated or
    re-encoded one.
    """
    payload = ""
    for feature in sample_result_set["features"]:
        for element in feature["elements"]:
            for hook in element.get("after", ()):
                for embedding in hook.get("embeddings", ()):
                    if embedding.get("mime_type") == "image/png":
                        payload = embedding["data"]
    assert payload, "the sample document must carry a PNG embedding"
    expected_uri = f"{PNG_DATA_URI_PREFIX}{payload}"

    pages = read_tree_pages(sample_tree)
    carrying = {
        SUITE_FEATURE_PAGES["Crm.feature"],
        SMOKE_TAG_PAGE,
        OVERVIEW_PAGES[3],
    }
    for name, page in pages.items():
        images = [
            reference.value
            for reference in page.references
            if reference.tag == "img" and is_data_uri(reference.value)
        ]
        if name in carrying:
            assert expected_uri in images, name
        else:
            assert images == [], name

    assert base64.b64decode(payload, validate=True).startswith(b"\x89PNG\r\n\x1a\n")


def test_no_emitted_page_carries_a_merge_conflict_marker(
    sample_tree: Path,
) -> None:
    """The committed reference tree is conflicted; a freshly written one is not.

    AAP 0.3.4 records nine marker blocks in one committed overview page, which
    is why no HTML golden fixture is mapped and why this is asserted directly
    on what the writer produces.
    """
    for name, page in read_tree_pages(sample_tree).items():
        for marker in CONFLICT_MARKERS:
            assert marker not in page.html, f"{name}: {marker}"


def test_project_name_and_build_date_reach_every_page(
    sample_result_set: Any, tmp_artifact_root: Path, pretty_env: Environment
) -> None:
    """The build-info table is page chrome: every page carries both cells.

    The project name is passed hostile on purpose, so this doubles as the
    escaping assertion for the one value that reaches every single page.
    """
    hostile_project = 'Acme & <Co> "QA" {{ 7*7 }}'

    root = pretty_reports.write_pretty_reports(
        sample_result_set,
        base=tmp_artifact_root,
        project_name=hostile_project,
        build_date=FIXED_BUILD_DATE,
        environment=pretty_env,
    )

    # What a template engine would have made of the same cell.  Compared as
    # the whole cell rather than as the bare "49", which occurs legitimately
    # inside the statistics tables' own numbers.
    evaluated = hostile_project.replace("{{ 7*7 }}", EVALUATED_EXPRESSION)

    for name, page in read_tree_pages(root).items():
        assert hostile_project in page.normalized_text, name
        assert FIXED_BUILD_DATE in page.normalized_text, name
        assert "<Co>" not in page.html, name
        assert evaluated not in page.normalized_text, name


def test_the_default_project_name_and_document_date_are_used(
    sample_tree: Path,
) -> None:
    """With neither passed, the pages carry the port's name and the document's.

    The build date comes from the document's ``generated_at``, which is what
    makes two renders of one document agree without a caller pinning it.
    """
    for name, page in read_tree_pages(sample_tree).items():
        assert pretty_reports.DEFAULT_PROJECT_NAME in page.normalized_text, name
        assert SAMPLE_BUILD_DATE in page.normalized_text, name


# --------------------------------------------------------------------------
# Status presentation: every branch, through the badge partial
# --------------------------------------------------------------------------

#: One scenario carrying a step in each of the seven statuses the result model
#: produces plus one the model never produces, which must fold to the
#: ``unknown`` fallback rather than to a pass.
ALL_STATUS_TOKENS: Final[tuple[str, ...]] = (
    "passed",
    "failed",
    "skipped",
    "pending",
    "undefined",
    "untested",
    "ambiguous",
    UNRECOGNISED_STATUS,
)


def all_status_document() -> dict[str, Any]:
    """A one-scenario document reaching every status branch.

    The sample document reaches passed, failed, skipped and undefined only,
    so pending, untested, ambiguous and the unknown fallback are driven from
    here.  The untested step deliberately carries no ``duration`` key, which
    is the shape the engine produces for it.
    """
    steps = tuple(
        build_step(
            f"a {token} step",
            token,
            duration=None if token == "untested" else index,
            line=10 + index,
            location=f"features.steps.synthetic_steps.step_{index}",
            error_message="synthetic failure" if token == "failed" else None,
        )
        for index, token in enumerate(ALL_STATUS_TOKENS)
    )
    return build_document(
        build_feature(
            "AllStatuses.feature",
            "Every status",
            (build_element("every status", steps),),
            tags=("@Status",),
        )
    )


def test_every_status_branch_renders_its_own_badge(
    pretty_env: Environment,
) -> None:
    """All eight status labels appear, each on a ``tqa-badge`` span.

    Status is read through the badge partial's stable output -- the class and
    the capitalised text content, which is the badge's accessible name --
    rather than through its attribute hook: the pages carry
    ``data-tqa-status`` today and a unified ``data-report-status`` after the
    shared-partials migration, and this module must not pin either spelling.
    """
    document = all_status_document()
    pages = parse_pages(
        pretty_reports.render_pretty_pages(document, environment=pretty_env)
    )
    feature_page = pages[
        pretty_reports.feature_page_name(f"{FEATURE_URI_PREFIX}AllStatuses.feature")
    ]

    assert set(STATUS_LABELS.values()) <= feature_page.badge_labels()
    for badge in feature_page.badges:
        assert BADGE_CLASS in badge.classes
        assert badge.label in set(STATUS_LABELS.values())


def test_each_badge_agrees_with_its_own_status_hook(
    pretty_env: Environment,
) -> None:
    """Where a badge carries a status attribute, it names the label's token.

    Either spelling of the hook is accepted -- see
    :data:`STATUS_HOOK_ATTRIBUTES` -- so the assertion is about the
    vocabulary, which this module owns, and not about the attribute name,
    which the shared-partials unit owns.
    """
    document = all_status_document()
    pages = parse_pages(
        pretty_reports.render_pretty_pages(document, environment=pretty_env)
    )
    feature_page = pages[
        pretty_reports.feature_page_name(f"{FEATURE_URI_PREFIX}AllStatuses.feature")
    ]

    assert feature_page.badges
    for badge in feature_page.badges:
        assert badge.status, badge
        assert badge.status in STATUS_LABELS
        assert STATUS_LABELS[badge.status] == badge.label


def test_an_unrecognised_status_is_reported_as_unknown_not_as_a_pass(
    pretty_env: Environment,
) -> None:
    """A status the model never produced must never be shown as green.

    ``status_token`` folds it to ``unknown`` and the page shows an
    ``Unknown`` badge; the stylesheet declares no rule for that token, so it
    resolves the neutral fallback -- visibly a status, visibly not one of the
    seven.
    """
    assert pretty_reports.status_token(UNRECOGNISED_STATUS) == "unknown"
    assert pretty_reports.status_token(None) == "unknown"
    assert pretty_reports.status_token("") == "unknown"
    assert pretty_reports.status_token("  Passed ") == "passed"

    document = build_document(
        build_feature(
            "Unknown.feature",
            "Unknown status",
            (
                build_element(
                    "one odd step",
                    (build_step("odd", UNRECOGNISED_STATUS, duration=1),),
                ),
            ),
        )
    )
    page = parse_page(
        "feature",
        pretty_reports.render_pretty_pages(document, environment=pretty_env)[
            pretty_reports.feature_page_name(f"{FEATURE_URI_PREFIX}Unknown.feature")
        ],
    )

    # The step itself is reported as Unknown, and the raw vocabulary word the
    # document carried never reaches the page in any form.  The scenario and
    # feature badges above it read Passed, because STATUS_PRECEDENCE names
    # only the seven statuses the model produces and worst_status answers its
    # ``empty`` default -- measured behaviour of the writer and the element
    # tree, owned by those units, so this module asserts the step badge that
    # is unambiguously this contract's.
    assert "Unknown" in page.badge_labels()
    assert UNRECOGNISED_STATUS not in page.normalized_text
    assert UNRECOGNISED_STATUS not in page.html
    unknown_badges = [badge for badge in page.badges if badge.label == "Unknown"]
    assert len(unknown_badges) == 1
    assert unknown_badges[0].status == "unknown"


def test_worst_status_folds_by_severity() -> None:
    """The precedence order, which decides a scenario's and a feature's status."""
    assert pretty_reports.worst_status(["passed", "skipped", "failed"]) == "failed"
    assert pretty_reports.worst_status(["passed", "undefined"]) == "undefined"
    assert pretty_reports.worst_status(["passed", "ambiguous"]) == "ambiguous"
    assert pretty_reports.worst_status(["passed", "pending"]) == "pending"
    assert pretty_reports.worst_status(["passed", "skipped"]) == "skipped"
    assert pretty_reports.worst_status(["passed", "untested"]) == "untested"
    assert pretty_reports.worst_status(["passed"]) == "passed"
    # A step-less element renders as passed, which is what the reference
    # generator does with the empty Background of EmployeeFc.feature.
    assert pretty_reports.worst_status([]) == "passed"
    assert pretty_reports.worst_status([], empty="unknown") == "unknown"
    assert pretty_reports.worst_status(["unknown"], empty="unknown") == "unknown"


def test_element_status_is_decided_by_the_element_s_own_steps() -> None:
    """A Background is not coloured by the scenario that follows it."""
    background = build_element(
        "a background",
        (build_step("sets up", "passed", duration=5),),
        element_type="background",
        keyword="Background",
    )
    scenario = build_element(
        "a scenario",
        (
            build_step("acts", "failed", duration=7, error_message="boom"),
            build_step("asserts", "skipped", duration=0),
        ),
    )

    assert pretty_reports.element_status(background) == "passed"
    assert pretty_reports.element_status(scenario) == "failed"
    assert pretty_reports.element_status(build_element("empty", ())) == "passed"


def test_element_duration_sums_step_durations_only() -> None:
    """Hook time is never added and a malformed duration counts as no sample.

    The after-hook that carries a failure screenshot must not lengthen its
    scenario, a skipped step legitimately carries no duration at all, and a
    boolean would otherwise add one nanosecond and hide a malformed document.
    """
    element = build_element(
        "mixed",
        (
            build_step("first", "passed", duration=2_415_000_000),
            build_step("second", "skipped"),
            build_step("third", "passed", duration=0),
        ),
    )
    element["after"] = [{"result": {"status": "passed", "duration": 412_000_000}}]

    assert pretty_reports.element_duration_ns(element) == 2_415_000_000
    assert pretty_reports.element_duration_ns(build_element("empty", ())) == 0
    assert (
        pretty_reports.element_duration_ns(
            {"steps": [{"result": {"duration": True}}, {"result": {"duration": -5}}]}
        )
        == 0
    )


def test_tag_rows_agree_cell_for_cell_with_the_tag_page(
    sample_result_set: Any, sample_tree: Path
) -> None:
    """The tags-overview row and the tag page's own tally are one rule.

    The row is built in Python and the page's own row is computed in the
    template, so the two are compared against each other here: a disagreement
    would put two different numbers for one tag in one artifact.
    """
    features = pretty_reports.emitted_features(sample_result_set)
    tags = pretty_reports.collect_tags(features)
    rows = pretty_reports.build_tag_rows(tags, pretty_reports.tag_href_map(tags))
    totals = pretty_reports.build_tag_totals(rows)

    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == SMOKE_TAG
    assert row["href"] == SMOKE_TAG_PAGE
    assert row["steps_passed"] == 5
    assert row["steps_failed"] == 2
    assert row["steps_skipped"] == 1
    assert row["steps_pending"] == 0
    assert row["steps_undefined"] == 0
    assert row["steps_total"] == 8
    assert row["scenarios_passed"] == 2
    assert row["scenarios_failed"] == 2
    assert row["scenarios_total"] == 4
    assert row["status"] == "failed"
    assert totals["features"] == 1
    assert totals["features_passed"] == 0
    assert totals["steps_total"] == row["steps_total"]
    assert totals["duration_ns"] == row["duration_ns"]

    overview = read_tree_pages(sample_tree)[OVERVIEW_PAGES[1]].normalized_text
    tag_page = read_tree_pages(sample_tree)[SMOKE_TAG_PAGE].normalized_text
    cells = f"{SMOKE_TAG} 5 2 1 0 0 8 2 2 4 20.477 Failed"
    assert cells in overview
    assert cells in tag_page


# --------------------------------------------------------------------------
# Escaping
# --------------------------------------------------------------------------


def hostile_document() -> dict[str, Any]:
    """A document whose every reader-visible string is hostile.

    One element, an attribute break, an entity and a Jinja expression in each
    of the feature name, the description, the tag, the scenario name, the step
    name, the matched argument and the error message -- the seven model
    positions that reach a page as text.
    """
    return build_document(
        build_feature(
            "Hostile.feature",
            HOSTILE_FEATURE_NAME,
            (
                build_element(
                    HOSTILE_SCENARIO_NAME,
                    (
                        build_step(
                            HOSTILE_STEP_NAME,
                            "failed",
                            duration=1,
                            error_message=HOSTILE_ERROR_MESSAGE,
                            arguments=(
                                {
                                    "val": HOSTILE_ARGUMENT,
                                    "offset": HOSTILE_ARGUMENT_OFFSET,
                                },
                            ),
                        ),
                    ),
                    description=HOSTILE_DESCRIPTION,
                ),
            ),
            tags=(HOSTILE_TAG,),
            description=HOSTILE_DESCRIPTION,
        )
    )


def test_hostile_result_data_is_escaped_on_every_page_that_carries_it(
    pretty_env: Environment,
) -> None:
    """Markup in the result model reaches the reader as text, never as markup.

    Every hostile value is asserted twice: absent from the raw markup in its
    dangerous spelling, and present in the parsed text in its original one.
    The second half is what stops the first from being satisfied by a writer
    that simply dropped the value.
    """
    document = hostile_document()
    pages = parse_pages(
        pretty_reports.render_pretty_pages(document, environment=pretty_env)
    )
    feature_page = pages[
        pretty_reports.feature_page_name(f"{FEATURE_URI_PREFIX}Hostile.feature")
    ]
    tag_page = pages[pretty_reports.tag_page_name(HOSTILE_TAG)]
    hostile_values = (
        HOSTILE_FEATURE_NAME,
        HOSTILE_SCENARIO_NAME,
        HOSTILE_STEP_NAME,
        HOSTILE_ERROR_MESSAGE,
        HOSTILE_DESCRIPTION,
    )

    # The dangerous spellings, not the bare tag names: every page carries
    # legitimate inline script of its own, which is what a page with no
    # external asset reference looks like.  These five fragments can only
    # come from the result model.
    dangerous = ("<script>alert", 'alert("f")', "alert('t')", "<i>x</i>", "<b>bold</b>")
    for name, page in pages.items():
        for fragment in dangerous:
            assert fragment not in page.html, f"{name}: {fragment}"

    # The same values, in the escaped spelling that makes them text.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in feature_page.html
    assert "&lt;i&gt;x&lt;/i&gt;" in feature_page.html
    assert "&lt;b&gt;bold&lt;/b&gt;" in feature_page.html

    for value in hostile_values:
        assert value in feature_page.normalized_text, value
    assert HOSTILE_TAG in feature_page.normalized_text
    assert HOSTILE_TAG in tag_page.normalized_text
    assert HOSTILE_SCENARIO_NAME in tag_page.normalized_text
    assert HOSTILE_FEATURE_NAME in pages[OVERVIEW_INDEX].normalized_text
    assert HOSTILE_ERROR_MESSAGE in pages[OVERVIEW_PAGES[3]].normalized_text


def test_the_matched_argument_is_spliced_as_escaped_text(
    pretty_env: Environment,
) -> None:
    """A matched argument is highlighted inside the step name, still escaped.

    The offsets tile the step name exactly, so the step row takes its inline
    splicing path -- the one that emits the argument as its own element -- and
    the hostile value travels through it as text.
    """
    document = hostile_document()
    pages = pretty_reports.render_pretty_pages(document, environment=pretty_env)
    raw = pages[
        pretty_reports.feature_page_name(f"{FEATURE_URI_PREFIX}Hostile.feature")
    ]
    page = parse_page("hostile-feature", raw)

    assert HOSTILE_ARGUMENT in page.normalized_text
    assert HOSTILE_STEP_NAME in page.normalized_text
    assert "<script>alert(1)</script>" not in raw
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in raw


def test_result_data_is_never_evaluated_as_a_template(
    pretty_env: Environment,
) -> None:
    """``{{ 7*7 }}`` in the model stays ``{{ 7*7 }}`` on the page.

    Autoescaping protects against markup; this protects against the other
    injection the engine makes possible -- data reaching a second render pass.
    Finding ``49`` anywhere would mean result data had been evaluated.
    """
    document = hostile_document()
    pages = parse_pages(
        pretty_reports.render_pretty_pages(document, environment=pretty_env)
    )

    for name, page in pages.items():
        assert EVALUATED_EXPRESSION not in page.normalized_text, name
        assert "{{" not in page.html.replace("{{ 7*7 }}", ""), name

    feature_page = pages[
        pretty_reports.feature_page_name(f"{FEATURE_URI_PREFIX}Hostile.feature")
    ]
    assert "{{ 7*7 }}" in feature_page.normalized_text


def test_hostile_pages_still_satisfy_the_structural_contract(
    tmp_artifact_root: Path, pretty_env: Environment
) -> None:
    """Hostile data must not break the tree it is rendered into.

    The same doctype, the same six pages, the same census and the same
    offline-resolution property as for the sample document: a value that
    escaped its attribute could otherwise produce a page whose references
    stopped resolving.
    """
    root = pretty_reports.write_pretty_reports(
        hostile_document(), base=tmp_artifact_root, environment=pretty_env
    )

    pages = read_tree_pages(root)
    assert set(pages) == {
        *OVERVIEW_PAGES,
        pretty_reports.feature_page_name(f"{FEATURE_URI_PREFIX}Hostile.feature"),
        pretty_reports.tag_page_name(HOSTILE_TAG),
    }
    assert asset_files(root) == EXPECTED_ASSETS
    for name, page in pages.items():
        assert page.declarations == ("DOCTYPE html",), name
        for value in resolvable_references(page):
            assert (root / split_reference(value)).is_file(), f"{name}: {value}"


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_render_is_pure_and_repeatable(
    sample_result_set: Any, tmp_artifact_root: Path, pretty_env: Environment
) -> None:
    """Two renders of one document are equal, and neither touches the disk.

    Purity is what lets the writer render everything before it creates a
    directory, which is why a template fault cannot leave a half-written tree.
    """
    first = pretty_reports.render_pretty_pages(
        sample_result_set, environment=pretty_env
    )
    second = pretty_reports.render_pretty_pages(
        sample_result_set, environment=pretty_env
    )

    assert first == second
    assert not paths.target_root(tmp_artifact_root).exists()
    assert list(tmp_artifact_root.iterdir()) == []


def test_writing_the_same_document_twice_is_byte_identical(
    sample_result_set: Any, tmp_artifact_root: Path, pretty_env: Environment
) -> None:
    """Idempotence: a second write leaves every one of the 31 files unchanged.

    No value is normalized away for this comparison and no build date is
    pinned: the sample document carries ``generated_at``, so the one value
    that could legitimately differ between two renders -- the build date, when
    the document carries no timestamp at all -- is fixed by the document
    itself.
    """
    root = pretty_reports.write_pretty_reports(
        sample_result_set, base=tmp_artifact_root, environment=pretty_env
    )
    before = tree_digests(root)

    again = pretty_reports.write_pretty_reports(
        sample_result_set, base=tmp_artifact_root, environment=pretty_env
    )
    after = tree_digests(again)

    assert again == root
    assert len(before) == len(SAMPLE_PAGES) + len(EXPECTED_ASSETS)
    assert before == after


def test_a_pinned_build_date_makes_a_dateless_document_deterministic(
    tmp_artifact_root: Path, tmp_path: Path, pretty_env: Environment
) -> None:
    """A document with no timestamp is the one case a caller must pin.

    ``format_build_date`` falls back to the current time, so two renders of a
    dateless document would differ in one cell.  Passing ``build_date``
    removes that freedom, and the two trees are then byte-identical.
    """
    dateless = {
        "features": [
            build_feature(
                "Dateless.feature",
                "No timestamps",
                (
                    build_element(
                        "a scenario",
                        (build_step("acts", "passed", duration=3),),
                        start_timestamp="",
                    ),
                ),
            )
        ]
    }
    second_root = tmp_path / "second-checkout"
    second_root.mkdir()

    first = pretty_reports.write_pretty_reports(
        dateless,
        base=tmp_artifact_root,
        build_date=FIXED_BUILD_DATE,
        environment=pretty_env,
    )
    second = pretty_reports.write_pretty_reports(
        dateless,
        base=second_root,
        build_date=FIXED_BUILD_DATE,
        environment=pretty_env,
    )

    assert tree_digests(first) == tree_digests(second)
    assert FIXED_BUILD_DATE in (first / OVERVIEW_INDEX).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# A second render over a smaller document
#
# The writer used to overwrite its tree in place and delete nothing, so a
# second render with fewer features left the earlier run's surplus detail
# pages on disk; it now publishes a staging tree by rename, so the page set is
# exactly the second document's.  The five assertions below were written to
# hold on both sides of that change and still do: they are about the tree a
# reader can reach and the references it carries, and the bound on the surplus
# -- which the swap has since reduced to nothing -- is stated as a bound so
# that it keeps describing the artifact rather than the mechanism.
# --------------------------------------------------------------------------


@pytest.fixture
def rerendered_tree(
    sample_result_set: Any, tmp_artifact_root: Path, pretty_env: Environment
) -> tuple[Path, dict[str, Any], frozenset[str], frozenset[str]]:
    """Write the four-feature document, then re-write a Crm-only one.

    :param sample_result_set: The parsed sample document.
    :param tmp_artifact_root: An empty checkout root.
    :param pretty_env: The shared template environment.
    :returns: ``(root, second document, first page set, second page set)``.
    """
    first_pages = frozenset(
        pretty_reports.render_pretty_pages(sample_result_set, environment=pretty_env)
    )
    root = pretty_reports.write_pretty_reports(
        sample_result_set, base=tmp_artifact_root, environment=pretty_env
    )
    assert page_files(root) == first_pages

    second_document = document_with_features(sample_result_set, "Crm.feature")
    second_pages = frozenset(
        pretty_reports.render_pretty_pages(second_document, environment=pretty_env)
    )
    again = pretty_reports.write_pretty_reports(
        second_document, base=tmp_artifact_root, environment=pretty_env
    )

    assert again == root
    assert second_pages < first_pages
    return root, second_document, first_pages, second_pages


def test_second_render_leaves_no_stale_content_in_a_current_page(
    rerendered_tree: tuple[Path, dict[str, Any], frozenset[str], frozenset[str]],
    pretty_env: Environment,
) -> None:
    """(a) Every page the second document demands exists and is current.

    Compared byte for byte against a fresh render of the second document, so a
    page left over *in content* -- an overview still tallying the features that
    went away -- fails here whether or not the surplus files were pruned.
    """
    root, document, _first, second_pages = rerendered_tree
    expected = pretty_reports.render_pretty_pages(document, environment=pretty_env)

    assert frozenset(expected) == second_pages
    for name, html in expected.items():
        page = root / name
        assert page.is_file(), name
        assert page.read_text(encoding="utf-8") == html, name


def test_second_render_keeps_the_asset_census_complete(
    rerendered_tree: tuple[Path, dict[str, Any], frozenset[str], frozenset[str]],
) -> None:
    """(b) All 22 assets are still present after the re-render.

    The asset copy runs again on every render, so this is what proves it is
    additive rather than destructive: no font is lost and no file is left
    truncated by the second pass.
    """
    root, _document, _first, _second = rerendered_tree

    assert asset_files(root) == EXPECTED_ASSETS
    for name in sorted(VENDORED_ASSETS):
        assert root.joinpath(*name.split("/")).read_bytes() == (
            paths.vendor_dir().joinpath(*name.split("/")).read_bytes()
        )


def test_second_render_leaves_no_dangling_reference_anywhere_in_the_tree(
    rerendered_tree: tuple[Path, dict[str, Any], frozenset[str], frozenset[str]],
) -> None:
    """(c) Every reference of every page in the tree still resolves to a file.

    Every page, including any the second render did not produce: a surplus
    page left behind must not be a broken one, because a reader who bookmarked
    it still opens it.
    """
    root, _document, _first, _second = rerendered_tree
    pages = read_tree_pages(root)
    assert pages

    for name, page in pages.items():
        for value in resolvable_references(page):
            resolved = (root / split_reference(value)).resolve()
            assert resolved.is_relative_to(root.resolve()), f"{name}: {value}"
            assert resolved.is_file(), f"{name}: {value}"


def test_second_render_overviews_reach_only_the_current_pages(
    rerendered_tree: tuple[Path, dict[str, Any], frozenset[str], frozenset[str]],
) -> None:
    """(d) Nothing stale is reachable from the tree's entry point.

    The four overview pages are the navigation surface: what they link is what
    a reader can get to.  Every detail page they name is one the second
    document demanded, so a surplus file on disk is unreachable rather than
    misleading.
    """
    root, _document, _first, second_pages = rerendered_tree
    pages = read_tree_pages(root)

    reachable: set[str] = set()
    for name in OVERVIEW_PAGES:
        for value in resolvable_references(pages[name]):
            if value.endswith(PAGE_SUFFIX):
                reachable.add(value)

    assert reachable <= second_pages
    assert SUITE_FEATURE_PAGES["Crm.feature"] in reachable
    assert SMOKE_TAG_PAGE in reachable
    for filename in ("Contact.feature", "Inventory.feature", "Sales.feature"):
        assert SUITE_FEATURE_PAGES[filename] not in reachable

    # Tightened from the four overviews to every page in the tree, surplus
    # pages included: a stale page must be unreachable from anywhere, not
    # merely absent from the navigation surface.  A surplus page still linking
    # its siblings would otherwise leave a reader a route into the previous
    # run's results from a file the current run never wrote.
    for name, page in pages.items():
        for value in resolvable_references(page):
            if not value.endswith(PAGE_SUFFIX):
                continue
            if name in second_pages:
                assert value in second_pages, f"{name} links surplus {value}"
            else:
                assert (root / value).is_file(), f"surplus {name} links dead {value}"


def test_second_render_adds_no_page_neither_render_produced(
    rerendered_tree: tuple[Path, dict[str, Any], frozenset[str], frozenset[str]],
) -> None:
    """(e) The surplus is bounded by what an earlier render itself wrote.

    Today the writer overwrites its tree in place and deletes nothing, so the
    surplus here is the three feature pages the smaller document no longer
    demands -- a strict subset of the first render's own page set.  Once the
    staging-and-atomic-replace change lands in
    ``app/reporting/pretty_reports.py`` the surplus becomes empty, and the
    subset relation holds just as it does now: an empty set is a subset of
    every set.  The assertion is written that way on purpose, so it gates the
    property that matters in both worlds -- the tree never holds a page
    neither render produced, and never a half-written one -- without pinning
    today's behaviour as required or forbidding tomorrow's.

    Every surplus page is also required to be a complete, parseable document,
    because an unpruned tree must not contain a truncated one.
    """
    root, _document, first_pages, second_pages = rerendered_tree
    pages = read_tree_pages(root)
    on_disk = frozenset(pages)
    surplus = on_disk - second_pages

    assert surplus <= first_pages
    assert on_disk <= first_pages | second_pages
    assert second_pages <= on_disk

    for name in sorted(surplus):
        page = pages[name]
        assert page.declarations == ("DOCTYPE html",), name
        assert page.title.strip(), name
        assert page.html.rstrip().endswith("</html>"), name
        for marker in CONFLICT_MARKERS:
            assert marker not in page.html, f"{name}: {marker}"


# --------------------------------------------------------------------------
# Forced failures
#
# The writer's contract with the run's exit table, in one sentence: every
# fault propagates -- producing this artifact is the writer's obligation and a
# swallowed fault would have the run report success over a missing report --
# and **no fault leaves a half-published tree**.  Because the tree is built in
# a staging sibling and swapped in by rename, what the destination holds after
# a fault is the previous complete generation or nothing, and never a mixture.
#
# Three faults are driven, all deterministically and none through a permission
# trick, which would not work in a container running as root anyway:
#
# * a render fault, from an environment whose loader holds no template;
# * an I/O fault part-way through the page loop, with no previous tree;
# * the same fault over a tree an earlier render published completely.
#
# The last two need a page write to fail *inside the staging tree*, and the
# staging tree is created by the call under test, so there is no path a test
# can occupy beforehand -- ``_recover_interrupted_publication`` clears this
# process's own scratch names on the way in, and a directory standing where a
# published page belongs is now simply replaced by the swap.  The fault is
# therefore injected at the writer's own ``open``: ``PageWriteFault`` below
# shadows the module global, which is where Python resolves the name first, and
# raises a genuine ``IsADirectoryError`` for one page and delegates every other
# call to the real builtin.  That is the same errno the previous mechanism
# produced, raised at the same point in the loop, and ``monkeypatch`` removes
# the shadow again when the test ends.
# --------------------------------------------------------------------------


class PageWriteFault:
    """A stand-in for :func:`open` that fails on one page and no other file.

    Installed over ``pretty_reports.open`` for the two I/O-fault tests.  Every
    call it does not target is delegated to the real builtin, so the asset copy
    and the pages before the target one are written exactly as they would be
    otherwise and the fault lands mid-loop rather than at the start of it.
    """

    def __init__(self, page_name: str) -> None:
        """Record which page must fail, and how many writes were attempted.

        :param page_name: The filename whose ``open`` raises.  Matched on the
            path's last component, so it matches wherever the writer is
            currently building -- the staging tree, whose name carries a
            process id a test has no reason to reconstruct.
        """
        self.page_name = page_name
        self.attempts = 0

    def __call__(self, file: Any, *args: Any, **kwargs: Any) -> Any:
        """Open ``file``, unless it is the page this instance fails on.

        :param file: The path the writer is opening.
        :param args: Positional arguments for the real :func:`open`.
        :param kwargs: Keyword arguments for the real :func:`open`.
        :returns: Whatever the real :func:`open` returns.
        :raises IsADirectoryError: When ``file`` names the target page.  The
            errno is the real one, so the writer sees an ordinary
            :class:`OSError` and cannot distinguish this from a filesystem
            that genuinely refused the write.
        """
        self.attempts += 1
        if Path(file).name == self.page_name:
            raise IsADirectoryError(
                errno.EISDIR, os.strerror(errno.EISDIR), str(file)
            )
        return open(file, *args, **kwargs)


def publication_scratch(final: Path) -> frozenset[str]:
    """Every publication scratch directory sitting beside ``final``.

    The writer builds in ``.<name>.staging-<pid>`` and moves the tree it is
    replacing to ``.<name>.superseded-<pid>``, both dot-prefixed so no HTTP
    request can reach them.  Neither may survive a call, so this is asserted
    empty after every fault.

    :param final: The published tree's directory.
    :returns: The names of the sibling directories whose name starts with a dot
        and the tree's own name, or an empty set when the parent is absent.
    """
    parent = final.parent
    if not parent.is_dir():
        return frozenset()
    return frozenset(
        entry.name
        for entry in parent.iterdir()
        if entry.name.startswith(f".{final.name}")
    )


def test_a_render_fault_raises_before_anything_is_written(
    sample_result_set: Any, tmp_artifact_root: Path, empty_template_env: Environment
) -> None:
    """A template that cannot be found is raised on, not swallowed.

    Nothing this writer would publish exists afterwards: the published tree is
    absent, not one file was written anywhere beneath the checkout root, and
    neither publication scratch directory survives.  Stated as "no file" rather
    than "no directory" because the staging tree is a sibling of the published
    one, so creating it creates the two directories above it -- an empty
    ``target/cucumber/`` is the parent of a location, which
    :mod:`app.utils.paths` creates and never removes, and it is not a partial
    artifact.  A *file* on disk after a failed publication would be, which is
    what this asserts against.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)

    with pytest.raises(TemplateNotFound):
        pretty_reports.write_pretty_reports(
            sample_result_set,
            base=tmp_artifact_root,
            environment=empty_template_env,
        )

    assert not final.exists()
    assert [
        path.relative_to(tmp_artifact_root).as_posix()
        for path in tmp_artifact_root.rglob("*")
        if path.is_file()
    ] == []
    assert publication_scratch(final) == frozenset()


def test_a_render_fault_leaves_a_previous_complete_tree_untouched(
    sample_result_set: Any,
    tmp_artifact_root: Path,
    pretty_env: Environment,
    empty_template_env: Environment,
) -> None:
    """The tree on disk survives a failed re-render byte for byte.

    This is what makes the artifact safe to publish: a later run that cannot
    render does not degrade the report an earlier run produced.
    """
    root = pretty_reports.write_pretty_reports(
        sample_result_set, base=tmp_artifact_root, environment=pretty_env
    )
    before = tree_digests(root)
    assert len(before) == len(SAMPLE_PAGES) + len(EXPECTED_ASSETS)

    with pytest.raises(TemplateNotFound):
        pretty_reports.write_pretty_reports(
            sample_result_set,
            base=tmp_artifact_root,
            environment=empty_template_env,
        )

    assert tree_digests(root) == before


def test_an_io_fault_part_way_through_publishes_nothing_at_all(
    sample_result_set: Any,
    tmp_artifact_root: Path,
    pretty_env: Environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The writer-failure exit class, driven at the last page of the loop.

    The fault is raised on the last page the loop reaches, so every earlier
    page and all 22 assets had already been written into the staging tree --
    which is precisely the state that used to become a half-published report.
    Nothing of it reaches the destination: the published directory does not
    exist, no page or asset file exists anywhere under the build output
    directory, and neither scratch directory is left behind.

    The fault itself still propagates.  Producing this artifact is the writer's
    contract with the run's exit table, whose writer-failure class names the
    failing writer on stderr, so an :class:`OSError` here must not be swallowed
    into a successful-looking run over a missing report.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    expected = pretty_reports.render_pretty_pages(
        sample_result_set, environment=pretty_env
    )
    assert tuple(expected)[-1] == SMOKE_TAG_PAGE, (
        "the fault must land on the last page of the loop"
    )
    fault = PageWriteFault(SMOKE_TAG_PAGE)
    monkeypatch.setattr(pretty_reports, "open", fault, raising=False)

    with pytest.raises(IsADirectoryError):
        pretty_reports.write_pretty_reports(
            sample_result_set, base=tmp_artifact_root, environment=pretty_env
        )

    # One open per page, so the fault really was reached at the end of a loop
    # that had already written every other page rather than at the start of it.
    assert fault.attempts == len(expected)
    assert not final.exists()
    assert publication_scratch(final) == frozenset()
    assert [
        path.relative_to(tmp_artifact_root).as_posix()
        for path in paths.target_root(tmp_artifact_root).rglob("*")
        if path.is_file()
    ] == []


def test_an_io_fault_leaves_a_previous_complete_tree_byte_for_byte(
    sample_result_set: Any,
    tmp_artifact_root: Path,
    pretty_env: Environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed render never degrades the report an earlier one published.

    The case the run's writer-failure exit class turns on: a tree was published
    completely, a later publication failed part-way, and a reader now opens
    what is there.  What is there is the earlier generation **in full** -- every
    page and every asset byte-identical, the whole file set unchanged -- rather
    than a mixture of two runs.  That is stronger than the per-page claim this
    test could make before the swap landed, and it is the claim the artifact
    contract actually needs: a reader cannot tell which pages a failed run
    happened to reach.

    The fault is injected as in the test above, on the last page of the loop,
    so the failing publication had a complete staging tree in hand and still
    published none of it.
    """
    first = pretty_reports.render_pretty_pages(
        sample_result_set, environment=pretty_env
    )
    root = pretty_reports.write_pretty_reports(
        sample_result_set, base=tmp_artifact_root, environment=pretty_env
    )
    assert page_files(root) == frozenset(first)
    before = tree_digests(root)

    # Same document, so the second publication would have written identical
    # bytes had it completed; the fault is the whole of what makes it fail.
    fault = PageWriteFault(SMOKE_TAG_PAGE)
    monkeypatch.setattr(pretty_reports, "open", fault, raising=False)

    with pytest.raises(IsADirectoryError):
        pretty_reports.write_pretty_reports(
            sample_result_set, base=tmp_artifact_root, environment=pretty_env
        )

    assert tree_digests(root) == before
    assert page_files(root) == frozenset(first)
    assert asset_files(root) == EXPECTED_ASSETS
    assert publication_scratch(root) == frozenset()


def test_a_missing_page_linked_asset_is_reported_as_a_writer_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page linking an asset that was not copied is a broken artifact.

    Driven by pointing the vendor accessor at a directory holding only some of
    the asset set, which is what a damaged installation looks like: the copy
    reports the dead references by name instead of writing pages that link
    nothing.  ``monkeypatch`` patches the writer's own imported reference, so
    ``app.utils.paths`` itself is left exactly as it is for every other test.
    """
    incomplete = tmp_path / "incomplete-vendor"
    (incomplete / "css").mkdir(parents=True)
    (incomplete / "css" / "cucumber.css").write_bytes(b"/* partial install */\n")
    monkeypatch.setattr(pretty_reports, "vendor_dir", lambda: incomplete)

    with pytest.raises(FileNotFoundError) as failure:
        pretty_reports.copy_pretty_assets(tmp_path / "broken-tree")

    message = str(failure.value)
    assert "css/bootstrap.min.css" in message
    assert "js/Chart.min.js" in message
    assert "images/favicon.png" in message
    # The port's own two assets come from app/static and were still copied,
    # so they are not among the names reported missing.
    assert "css/main.css" not in message
    assert "js/report.js" not in message


def test_a_font_that_was_not_copied_is_a_writer_failure_like_any_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing icon font fails the artifact; it does not merely warn.

    A font is requested from a stylesheet rather than from a page, which used
    to be the argument for reporting the gap and writing the tree anyway.  It
    is the wrong conclusion, and the writer no longer draws it: the stylesheet
    doing the requesting is a file this writer itself copies into the tree, so
    a missing font is the writer publishing a reference to something it knows
    is not there -- a dangling reference in a published artifact, which is the
    one thing an offline report cannot survive.  All eleven fonts are therefore
    as mandatory as the page-linked assets, and the failure names every asset
    that would have dangled so the cause is in the log rather than in a
    reader's browser console.

    Driven by pointing the vendor accessor at a tree holding every page-linked
    file and no font at all, so the page-linked half of the manifest is
    satisfied and the fonts are the whole of what the message can be about.
    """
    partial = tmp_path / "fontless-vendor"
    for name in pretty_reports.PAGE_LINKED_ASSETS:
        if name in {relative for relative, _ in pretty_reports.PORT_ASSETS}:
            # Supplied from app/static by the writer itself, not from here.
            continue
        target = partial.joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"/* stand-in */\n")
    monkeypatch.setattr(pretty_reports, "vendor_dir", lambda: partial)

    root = tmp_path / "fontless-tree"
    with pytest.raises(FileNotFoundError) as failure:
        pretty_reports.copy_pretty_assets(root)

    message = str(failure.value)
    assert str(root) in message
    # Every font is named, not just the first one found missing: a broken
    # installation is diagnosed once rather than one file per attempt.
    for name in pretty_reports.VENDORED_FONT_ASSETS:
        assert name in message, name
    assert str(len(pretty_reports.VENDORED_FONT_ASSETS)) in message
    # The page-linked assets were all present, so none of them is reported.
    for name in pretty_reports.PAGE_LINKED_ASSETS:
        assert name not in message, name
    # And the tree that would have dangled is not a tree anyone can publish:
    # no font reached it, which is exactly what the failure is about.
    assert not any(name.startswith("fonts/") for name in asset_files(root))


def test_the_shared_partials_reference_nothing_outside_the_tree(
    pretty_env: Environment,
) -> None:
    """Each shared partial stays inside the tree on its own, not only in context.

    The three partials under ``partials/`` render into this tree, into the
    single self-contained page and into the framework views, and those surfaces
    do not agree about assets: here every reference has to resolve inside the
    emitted directory, because the pages are opened straight from a build
    workspace with no server and no network.  A partial that acquired an
    endpoint URL, a CDN address or a ``/``-rooted path would look correct in a
    view and break every page of this tree.

    The whole-tree walk above can only catch that where the sample renders the
    offending branch.  This renders every macro of every partial directly,
    across the parameter space each exposes, and holds each fragment to the
    tree's own rule: nothing absolute, nothing protocol-relative, nothing
    ``/``-rooted, and no filesystem reference at all beyond an inline payload.
    """
    badges = pretty_env.get_template("partials/status_badge.html").module
    rows = pretty_env.get_template("partials/step_row.html").module
    lightbox = pretty_env.get_template("partials/lightbox.html").module

    step = build_step("a step", "failed", 1, error_message="boom")
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\n synthetic").decode("ascii")
    embedding = {"mime_type": "image/png", "data": payload, "name": "a shot"}
    fragments: list[str] = [
        str(badges.status_badge(status, extra_classes=status))  # type: ignore[attr-defined]
        for status in (*pretty_reports.KNOWN_STATUSES, pretty_reports.UNKNOWN_STATUS)
    ]
    fragments.append(str(rows.step_row(step)))  # type: ignore[attr-defined]
    fragments.append(str(rows.step_row(step, uid="u1", show_location=True)))  # type: ignore[attr-defined]
    fragments.append(str(rows.step_row(step, embeddings=[embedding])))  # type: ignore[attr-defined]
    fragments.append(str(lightbox.screenshot(embedding, caption="A caption")))  # type: ignore[attr-defined]
    fragments.append(str(lightbox.screenshots([embedding])))  # type: ignore[attr-defined]
    fragments.append(str(lightbox.screenshots([])))  # type: ignore[attr-defined]
    fragments.append(str(lightbox.lightbox_overlay(label="Screenshot")))  # type: ignore[attr-defined]

    # The eight status tokens plus three step rows and four lightbox fragments.
    assert len(fragments) == 15
    for fragment in fragments:
        page = parse_page("fragment", fragment)
        assert resolvable_references(page) == (), fragment
        assert not any(is_external(value) for value in fragment.split('"')), fragment
    # The positive half: an attachment does reach the markup as an inline
    # payload, so "references nothing" is not met by emitting nothing.
    assert any(PNG_DATA_URI_PREFIX in fragment for fragment in fragments)


# --------------------------------------------------------------------------
# Malformed documents
#
# Every branch below is a document the merge should never produce, and each one
# has an answer in the writer rather than an exception: an artifact is what a
# reader turns to when a run has gone wrong, so a damaged result set has to
# render.  These are the writer's own defensive paths, driven deliberately.
# --------------------------------------------------------------------------


def test_a_feature_without_a_uri_is_listed_but_gets_no_detail_page(
    pretty_env: Environment, caplog: pytest.LogCaptureFixture
) -> None:
    """A feature with no URI has no hash input, so it has no page to link.

    It still appears on the features overview, because dropping a feature from
    the report would hide a result that ran; what it loses is only its link.
    The page set therefore holds one detail page for the feature that has a
    URI and none for the one that does not.
    """
    named = build_feature(
        "Crm.feature",
        "Testinium app CRM Module",
        (build_element("has a uri", (build_step("a passing step", "passed", 1),)),),
    )
    anonymous = build_feature(
        "Orphan.feature",
        "Feature without a uri",
        (build_element("no uri", (build_step("a passing step", "passed", 1),)),),
    )
    del anonymous["uri"]
    document = build_document(named, anonymous)

    with caplog.at_level("WARNING", logger=pretty_reports.logger.name):
        pages = pretty_reports.render_pretty_pages(document, environment=pretty_env)

    detail_pages = [name for name in pages if name.startswith(FEATURE_PAGE_PREFIX)]
    assert detail_pages == [SUITE_FEATURE_PAGES["Crm.feature"]]
    overview = parse_page(OVERVIEW_INDEX, pages[OVERVIEW_INDEX])
    assert "Feature without a uri" in overview.text
    assert "Testinium app CRM Module" in overview.text
    assert SUITE_FEATURE_PAGES["Crm.feature"] in resolvable_references(overview)
    warnings = [record.getMessage() for record in caplog.records]
    assert any("no uri" in message for message in warnings), warnings


def test_two_features_sharing_one_uri_yield_one_page_and_a_warning(
    pretty_env: Environment,
    tmp_artifact_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One URI is one page, and the collision is reported rather than invented.

    A correct merge keys one feature object per path, so two features on one
    URI cannot come out of it.  The writer has no disambiguator to reach for --
    the reference generator's numeration applies only across separate input
    files -- so **the later page wins** and the collision is named on the log.

    Last-write-wins is the rule on both sides of the writer and that is why it
    is asserted rather than merely tolerated: ``render_pretty_pages`` is
    ``dict(iter_pretty_pages(...))``, where a repeated key keeps the last
    value, and ``write_pretty_reports`` writes the pages in the same order into
    one filename, where a repeated name overwrites.  Were the two to disagree,
    the page a reader opens would differ from the page this module asserts on.
    The reference generator resolves it the same way, writing its pages
    sequentially with no disambiguator either.

    This is distinct from the id collision two pairs of this suite's features
    genuinely have, which is preserved and does *not* merge their pages.
    """
    first = build_feature(
        "Crm.feature",
        "First on this uri",
        (build_element("first", (build_step("a passing step", "passed", 1),)),),
    )
    second = build_feature(
        "Crm.feature",
        "Second on this uri",
        (build_element("second", (build_step("a passing step", "passed", 1),)),),
    )
    document = build_document(first, second)

    with caplog.at_level("WARNING", logger=pretty_reports.logger.name):
        pages = pretty_reports.render_pretty_pages(document, environment=pretty_env)

    detail_pages = [name for name in pages if name.startswith(FEATURE_PAGE_PREFIX)]
    assert detail_pages == [SUITE_FEATURE_PAGES["Crm.feature"]]
    detail = parse_page(
        SUITE_FEATURE_PAGES["Crm.feature"],
        pages[SUITE_FEATURE_PAGES["Crm.feature"]],
    )
    assert "Second on this uri" in detail.text
    assert "First on this uri" not in detail.text
    warnings = [record.getMessage() for record in caplog.records]
    assert any(
        SUITE_FEATURE_PAGES["Crm.feature"] in message for message in warnings
    ), warnings
    assert any("later page wins" in message for message in warnings), warnings

    # The other side of the rule: one filename on disk, holding the same page
    # the pure render returned.  A writer that resolved the collision
    # differently -- keeping the first, or emitting a second filename -- would
    # hand a reader a page no assertion above has seen.
    root = pretty_reports.write_pretty_reports(
        document, base=tmp_artifact_root, environment=pretty_env
    )
    on_disk = [
        name for name in page_files(root) if name.startswith(FEATURE_PAGE_PREFIX)
    ]
    assert on_disk == [SUITE_FEATURE_PAGES["Crm.feature"]]
    assert (root / SUITE_FEATURE_PAGES["Crm.feature"]).read_text(
        encoding="utf-8"
    ) == pages[SUITE_FEATURE_PAGES["Crm.feature"]]


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        ("2022-09-07T13:39:04.123Z", "07 Sep 2022, 13:39"),
        ("2022-09-07T13:39:04.123+00:00", "07 Sep 2022, 13:39"),
        ("2022-09-07T13:39:04.123", "07 Sep 2022, 13:39"),
    ],
    ids=["zulu", "offset", "naive-read-as-utc"],
)
def test_the_build_date_accepts_every_timestamp_shape_the_schema_allows(
    stamp: str, expected: str
) -> None:
    """A naive timestamp is read as UTC, which is what the collector writes.

    The three shapes are the ones a document can legitimately carry: the
    collector's own trailing ``Z``, an explicit offset, and a timestamp written
    without a zone by an older or hand-edited producer.  All three must reach
    the same Date cell, because the same run must not date itself differently
    depending on which producer wrote its document.
    """
    assert pretty_reports.format_build_date({"generated_at": stamp}) == expected


def test_an_unparseable_build_date_is_kept_verbatim(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What the document says survives when it cannot be parsed.

    Substituting the current time would date the report to the moment it was
    rendered and quietly discard what the run recorded, so the raw text is
    carried through to the Date cell instead and the failure to parse it is
    logged.  ``started_at`` is not consulted, because the first key present is
    the one the document meant.
    """
    document = {"generated_at": "not a timestamp", "started_at": "also not one"}

    with caplog.at_level("WARNING", logger=pretty_reports.logger.name):
        assert pretty_reports.format_build_date(document) == "not a timestamp"

    warnings = [record.getMessage() for record in caplog.records]
    assert any("not a timestamp" in message for message in warnings), warnings


def test_an_unparseable_build_date_reaches_the_pages_it_dates(
    pretty_env: Environment,
) -> None:
    """The verbatim date is what the build-info table on every page shows.

    The unit above fixes the value; this one proves it is the value the reader
    sees, on all four overview pages, so the two cannot drift apart.
    """
    document = build_document(
        build_feature(
            "Crm.feature",
            "Testinium app CRM Module",
            (build_element("a scenario", (build_step("a passing step", "passed", 1),)),),
        )
    )
    document["generated_at"] = "not a timestamp"
    document["started_at"] = ""

    pages = pretty_reports.render_pretty_pages(document, environment=pretty_env)

    for name in OVERVIEW_PAGES:
        assert "not a timestamp" in parse_page(name, pages[name]).text, name
