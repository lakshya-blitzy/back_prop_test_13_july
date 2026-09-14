"""Tests for HTML contract 2: the emitted PrettyReports tree.

This module is the gate for ``app/reporting/pretty_reports.py`` and for the
``app/templates/pretty/`` templates it renders, read as the *artifact* they
produce rather than as the functions producing it.  The destination is
``CukesRunner.java:13``; the artifact is a directory a Jenkins agent opens
straight from a workspace over the file protocol, and AAP 0.3.4 holds it to:

1. **Exact filenames.**  The detail pages are named by a numeric hash the Java
   generator computed (``net.masterthought.cucumber.util.Util.toValidFileName``
   over a feature's ``file:`` URI or a tag's ``@``-prefixed name), so every
   expected name in this module is a pinned literal rather than a pattern.  A
   filename nobody can predict is a filename no published report can link.
2. **Page cardinality.**  The four overview pages, always; one feature page per
   emitted feature in source order; one tag page per tag of the run; and, above
   the writer's aggregate fan-out budgets, no tree at all.
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
   between two renders, a document whose page fan-out or output size is beyond
   what this writer will produce, a render fault and an I/O fault part-way
   through the page loop.

Two contracts every assertion here is written against.

``write_pretty_reports`` publishes a validated staging tree by rename
    The writer builds the whole tree in a dot-prefixed staging sibling,
    verifies the inventory there, and swaps it into place with two renames, so
    a reader sees one complete generation or none and never a mixture, and a
    page whose feature or tag has disappeared is gone rather than merely
    unreachable.  A second render over a smaller document therefore publishes
    exactly that document's page set.

    What follows for this module is that a fault leaves **no partial tree to
    inspect**: the three forced-failure tests below assert what the destination
    holds after a fault, which is either the previous complete tree byte for
    byte or nothing at all, and no publication scratch either way.  The rest of
    the tree contract is asserted on the published tree as a whole: current
    pages carry current content, the census is complete, no reference anywhere
    dangles, and no page links or is anything the current document did not
    produce.

The publication is descriptor-bound, and its output is owner-only
    Every filesystem step of the publication -- the staging directory, each
    asset copy, each page write, both renames of the swap and each scratch
    removal -- now runs relative to a directory descriptor
    ``app/utils/paths.py`` verified, through the
    ``ArtifactDirectoryPublication`` that module publishes, instead of being
    re-resolved from a pathname after the check that approved it.  The writer
    therefore no longer derives a staging path, no longer copies with
    ``shutil``, and no longer removes anything by walking a directory it
    reached by name -- the probe that closed this gap redirected a
    path-resolved cleanup into a prepared directory *outside* the artifact root
    and deleted it.

    What that adds to this module: the census and the page set are asserted
    through a publication object wherever the copy is exercised on its own; the
    published tree is asserted to be **owner-only**, every directory ``0700``
    and every page and asset ``0600``, including the copy of a vendored asset
    whose source is group-readable; a link standing where the published tree or
    an owned directory component belongs is asserted to be *refused*; and a
    symbolic link planted inside a scratch tree is asserted to be unlinked
    rather than followed, with a prepared directory outside the artifact root
    still intact afterwards.  The mode assertions compare the permission bits
    only: a build output directory that is set-group-id stays set-group-id, it
    simply grants the group nothing.

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
import shutil
import stat
import zlib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Final

import pytest
from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from app.reporting import aggregation, pretty_reports
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

#: A process id this process is certainly not, for the publication-scratch
#: recovery rules: scratch carrying it must be left exactly where it is,
#: because a live publication in another process may still own it.  Its value
#: is immaterial -- ``PublicationScratch.is_own`` compares names, so any id but
#: this process's is another process's -- so it is a fixed implausible number
#: rather than a real pid, which would risk naming a process that exists.
FOREIGN_PID: Final[int] = 999999999

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

#: ``java.lang.Integer.MIN_VALUE``, the hash whose offset segment is ``-1``.
INT32_MIN: Final[int] = -2147483648

#: The inclusive bounds of the offset filename segment: the signed hash plus
#: ``Integer.MAX_VALUE``, computed in 64 bits as the generator computes it.
MIN_PAGE_HASH: Final[int] = INT32_MIN + INT32_MAX
MAX_PAGE_HASH: Final[int] = 2 * INT32_MAX

#: The documented string whose Java hash is ``Integer.MIN_VALUE``, and the two
#: detail-page names it produces.  Both are emitted names, so both are
#: destinations ``local_page_href`` must admit; a filename the writer emits and
#: the allowlist refuses is a page in the tree that nothing links.
MIN_VALUE_HASH_INPUT: Final[str] = "polygenelubricants"
MIN_VALUE_FEATURE_PAGE: Final[str] = (
    f"{FEATURE_PAGE_PREFIX}{MIN_PAGE_HASH}{PAGE_SUFFIX}"
)
MIN_VALUE_TAG_PAGE: Final[str] = f"{TAG_PAGE_PREFIX}{MIN_PAGE_HASH}{PAGE_SUFFIX}"

#: A tag name -- leading ``@`` included, as the model carries it -- whose own
#: Java hash is ``Integer.MIN_VALUE``.  It is what lets the negative page name
#: be driven through a whole render rather than only through the two name
#: functions, and the hash is asserted rather than trusted wherever it is used.
MIN_VALUE_TAG: Final[str] = "@DEUFDHV"

#: Inputs whose Java ``String.hashCode`` is a fixed point of the algorithm's
#: documented edge cases, as ``(text, signed hash, offset filename segment)``.
HASH_EDGE_CASES: Final[tuple[tuple[str, int, str], ...]] = (
    ("", 0, "2147483647"),
    (SMOKE_TAG, 1912275215, "4059758862"),
    # The Integer.MIN_VALUE case: the JVM's 32-bit overflow is load-bearing,
    # and a mask-only implementation would answer 4294967295 here.
    (MIN_VALUE_HASH_INPUT, INT32_MIN, "-1"),
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

#: A genuinely well-formed 1x1 PNG payload -- the same bytes
#: ``tests/conftest.py`` hands its stub driver, base64 as an embedding carries
#: them.  Every probe here that expects a screenshot to *render* uses it,
#: because the lightbox partial and the writers both apply the inline-PNG
#: contract owned by ``app/reporting/screenshots.py``: a payload that merely
#: begins with PNG's signature renders as nothing at all, and an assertion
#: about the rendered image would then pass without the image.
VALID_PNG_PAYLOAD: Final[str] = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP4z8DwHwAFAAH/"
    "VscvDQAAAABJRU5ErkJggg=="
)

#: The embedding's declared MIME type, and the eight-byte signature its decoded
#: payload must begin with.  ``app/reporting/screenshots.py`` drops an
#: attachment that fails either check, so a synthetic screenshot built here has
#: to satisfy both to reach a page at all.
PNG_MIME_TYPE: Final[str] = "image/png"
PNG_SIGNATURE: Final[bytes] = b"\x89PNG\r\n\x1a\x0a"

#: What :func:`padded_png_bytes` needs to build a large screenshot the
#: inline-PNG contract accepts: the width of each of a chunk's three fields,
#: the twelve bytes an empty ``IEND`` chunk occupies at the end of every
#: well-formed PNG, and the ancillary chunk the padding rides in with its
#: keyword.  ``tEXt`` is on that contract's allowlist of safe, uncompressed
#: ancillary types, so a screenshot padded through it is still a screenshot.
PNG_CHUNK_FIELD_BYTES: Final[int] = 4
PNG_END_CHUNK_BYTES: Final[int] = 12
PNG_TEXT_CHUNK: Final[bytes] = b"tEXt"
PNG_TEXT_KEYWORD: Final[bytes] = b"Comment"

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
#:
#: It was ``"executing"`` until the shared status model took in the behave-only
#: spellings: ``app/reporting/aggregation.py``'s ``STATUS_ALIASES`` folds
#: ``executing`` onto ``untested``, because it is behave's own name for a step
#: whose outcome was never established, so that word is now a *recognised*
#: status rather than an unrecognised one and drives the alias fold instead of
#: the fallback.  This value belongs to no tool's vocabulary and no alias
#: table, which is the whole of what this constant needs to be: the fallback is
#: reached for a status nothing in the project can read, and a word any table
#: claims cannot exercise it.
UNRECOGNISED_STATUS: Final[str] = "no-such-status"

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
    the input that shows whether a published tree is replaced or added to.

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
# Table reading
#
# A page's visible text is enough for a value that appears on it, which is what
# most of the content assertions below read.  A per-row *status* is not: it
# lives on the cell's class and hook attribute rather than in its text, and the
# cell it belongs to has to be tied back to the row that owns it.  These
# helpers read one named table into rows and cells, so an assertion can name
# the row it is about, and they are deliberately as neutral as
# :class:`_PageCollector` -- no judgement, only structure.
# --------------------------------------------------------------------------

#: The ``id`` of the steps-overview statistics table, which the layout's
#: sorter initialisation binds to and which distinguishes it from the
#: build-info table every page's layout also emits.
STEPS_TABLE_ID: Final[str] = "tablesorter"


@dataclass(frozen=True)
class Cell:
    """One ``th`` or ``td``, as a reader and the stylesheet see it.

    :ivar tag: ``"th"`` or ``"td"``.
    :ivar text: Its text content, with runs of whitespace collapsed and the
        edges stripped, so an assertion is about the value and not about the
        template's line breaks.
    :ivar classes: Its class tokens, the bare status word among them.
    :ivar status: The value of whichever status hook the cell carries, or
        ``""`` when it carries neither spelling -- see
        :data:`STATUS_HOOK_ATTRIBUTES`.
    """

    tag: str
    text: str
    classes: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class TableRow:
    """One ``tr`` of a named table.

    :ivar section: ``"thead"``, ``"tbody"`` or ``"tfoot"``, so a footer
        assertion cannot be satisfied by a body row or the reverse.
    :ivar cells: Its cells in column order, header cell included.
    """

    section: str
    cells: tuple[Cell, ...]

    @property
    def texts(self) -> tuple[str, ...]:
        """Every cell's text, in column order."""
        return tuple(cell.text for cell in self.cells)

    @property
    def last(self) -> Cell:
        """The row's final cell, which is where these tables put the status.

        :returns: The last cell.
        :raises AssertionError: If the row has none, in which case no
            assertion about "the" status cell would mean anything.
        """
        assert self.cells, f"a {self.section} row with no cells has no status cell"
        return self.cells[-1]


class _TableCollector(HTMLParser):
    """Collect the rows of the one table carrying ``table_id``."""

    _CELL_TAGS: Final[frozenset[str]] = frozenset({"th", "td"})
    _SECTION_TAGS: Final[frozenset[str]] = frozenset({"thead", "tbody", "tfoot"})

    def __init__(self, table_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self._table_id = table_id
        self.rows: list[TableRow] = []
        self._inside = False
        self._section = ""
        self._cells: list[Cell] | None = None
        self._cell: tuple[str, dict[str, str], list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Enter the named table, a section, a row or a cell."""
        attributes = {name: (value if value is not None else "") for name, value in attrs}
        if tag == "table":
            self._inside = attributes.get("id") == self._table_id
            return
        if not self._inside:
            return
        if tag in self._SECTION_TAGS:
            self._section = tag
        elif tag == "tr":
            self._cells = []
        elif tag in self._CELL_TAGS and self._cells is not None:
            self._cell = (tag, attributes, [])

    def handle_endtag(self, tag: str) -> None:
        """Close whichever structure ``tag`` opened, and bank a finished row."""
        if tag == "table":
            self._inside = False
            return
        if not self._inside:
            return
        if tag in self._CELL_TAGS and self._cell is not None:
            cell_tag, attributes, parts = self._cell
            status = ""
            for hook in STATUS_HOOK_ATTRIBUTES:
                if hook in attributes:
                    status = attributes[hook]
                    break
            assert self._cells is not None
            self._cells.append(
                Cell(
                    tag=cell_tag,
                    text=re.sub(r"\s+", " ", "".join(parts)).strip(),
                    classes=tuple(attributes.get("class", "").split()),
                    status=status,
                )
            )
            self._cell = None
        elif tag == "tr" and self._cells is not None:
            self.rows.append(
                TableRow(section=self._section, cells=tuple(self._cells))
            )
            self._cells = None

    def handle_data(self, data: str) -> None:
        """Route character data to the open cell, if any."""
        if self._cell is not None:
            self._cell[2].append(data)


def table_rows(page: ParsedPage, table_id: str) -> tuple[TableRow, ...]:
    """Every row of the table carrying ``table_id``, in document order.

    :param page: The parsed page, read for its exact markup.
    :param table_id: The table's ``id``.
    :returns: Its rows, header, body and footer alike.
    """
    collector = _TableCollector(table_id)
    collector.feed(page.html)
    collector.close()
    return tuple(collector.rows)


def steps_body_rows(page: ParsedPage) -> dict[str, TableRow]:
    """The steps overview's body rows, keyed by their implementation.

    The first cell of each row is the implementation, which is that row's own
    header cell, so keying by it is how an assertion names the row it is
    about.

    :param page: The parsed ``overview-steps.html``.
    :returns: Implementation to row.
    """
    return {
        row.cells[0].text: row
        for row in table_rows(page, STEPS_TABLE_ID)
        if row.section == "tbody" and row.cells
    }


def steps_footer_row(page: ParsedPage) -> TableRow:
    """The steps overview's single footer row.

    :param page: The parsed ``overview-steps.html``.
    :returns: The footer row.
    :raises AssertionError: If the page carries none or more than one.
    """
    footers = [
        row for row in table_rows(page, STEPS_TABLE_ID) if row.section == "tfoot"
    ]
    assert len(footers) == 1, f"{page.name} carries {len(footers)} footer rows"
    return footers[0]


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


def permission_bits(path: Path) -> int:
    """The permission bits of ``path``, read without following a link.

    The special bits are deliberately excluded.  A build output directory that
    the operator made set-group-id stays set-group-id -- the writer's mode
    policy clears the group and other *permission* bits and leaves the special
    ones alone -- so an assertion that compared the whole mode would fail on a
    workspace mounted under a setgid directory while the property under test
    held perfectly.

    :param path: The entry to read.
    :returns: ``st_mode`` masked to ``0o777``.
    """
    return stat.S_IMODE(path.lstat().st_mode) & 0o777


def tree_permissions(root: Path) -> dict[str, int]:
    """Every entry under ``root`` mapped to its permission bits.

    :param root: The tree to walk, itself excluded.
    :returns: Tree-relative POSIX path to permission bits.
    """
    return {
        path.relative_to(root).as_posix(): permission_bits(path)
        for path in sorted(root.rglob("*"))
    }


def staged_publication(final: Path) -> paths.ArtifactDirectoryPublication:
    """Begin a publication of ``final`` with its staging tree created.

    The two calls every exercise of the asset copy on its own needs, since
    :func:`app.reporting.pretty_reports.copy_pretty_assets` now takes the
    publication that owns the destination rather than a destination path: the
    files it writes have to land under a verified staging descriptor, and only
    the publication holds one.

    The caller closes it -- ``with staged_publication(...) as publication`` --
    which releases the descriptors and removes nothing.

    :param final: The directory the publication would publish.
    :returns: The publication, staged.
    """
    publication = paths.begin_directory_publication(final)
    publication.create_staging()
    return publication


def scratch_name(final: Path, infix: str, pid: int) -> str:
    """The name a publication of ``final`` gives one of its scratch trees.

    Reproduced from the path authority's own exported infixes rather than from
    a literal, so a test that plants scratch by hand plants the name the
    publication will recognise -- which is the whole of what makes an
    interrupted publication recoverable.

    :param final: The published tree's directory.
    :param infix: :data:`app.utils.paths.PUBLICATION_STAGING_INFIX` or
        :data:`~app.utils.paths.PUBLICATION_SUPERSEDED_INFIX`.
    :param pid: The process id the name carries.  ``os.getpid()`` for
        scratch this process owns, :data:`FOREIGN_PID` for another's.
    :returns: The scratch directory's final component.
    """
    return f".{final.name}{infix}{pid}"


def plant_scratch(final: Path, name: str, marker: str = "marker.txt") -> Path:
    """Create a scratch directory beside ``final`` holding one marker file.

    The state an interrupted publication leaves behind, built by hand so the
    recovery rules can be asserted without killing a process.  The marker is
    what an assertion reads to tell a restored tree from a freshly written one.

    :param final: The published tree's directory.
    :param name: The scratch directory's name, from :func:`scratch_name`.
    :param marker: Name of the file written inside it.
    :returns: The scratch directory.
    """
    directory = final.with_name(name)
    directory.mkdir(parents=True)
    (directory / marker).write_text(name, encoding="utf-8")
    return directory


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
# The link allowlist
#
# ``local_page_href`` is the one check every dynamic href of the six page
# templates passes through, and its contract is an equivalence rather than a
# resemblance: the names it admits are exactly the names the writer emits.
# Both directions are gated here, because each failure is its own defect --
# admitting more than the writer emits is an open link sink fed by a result
# document, and admitting less strips the link to a page that is in the tree
# and leaves its label as plain text.
# --------------------------------------------------------------------------


def test_the_minimum_value_hash_names_are_the_two_negative_page_names() -> None:
    """The one input whose page names carry a minus sign, named exactly.

    ``polygenelubricants`` hashes to ``Integer.MIN_VALUE``, whose offset
    segment is ``-1``, so the writer's own two name functions produce
    ``report-feature_-1.html`` and ``report-tag_-1.html``.  Asserted here as
    literals because the rest of this section is about those two names being
    reachable destinations.
    """
    assert pretty_reports.java_hash_code(MIN_VALUE_HASH_INPUT) == INT32_MIN
    assert pretty_reports.to_valid_file_name(MIN_VALUE_HASH_INPUT) == str(
        MIN_PAGE_HASH
    )
    assert pretty_reports.feature_page_name(MIN_VALUE_HASH_INPUT) == (
        MIN_VALUE_FEATURE_PAGE
    )
    assert pretty_reports.tag_page_name(MIN_VALUE_HASH_INPUT) == MIN_VALUE_TAG_PAGE
    assert MIN_VALUE_FEATURE_PAGE == "report-feature_-1.html"
    assert MIN_VALUE_TAG_PAGE == "report-tag_-1.html"


@pytest.mark.parametrize(
    "name",
    [
        *OVERVIEW_PAGES,
        *sorted(SUITE_FEATURE_PAGES.values()),
        SMOKE_TAG_PAGE,
        # The whole range of the offset hash, at both ends and at the one
        # value that carries a sign.
        MIN_VALUE_FEATURE_PAGE,
        MIN_VALUE_TAG_PAGE,
        f"{FEATURE_PAGE_PREFIX}0{PAGE_SUFFIX}",
        f"{TAG_PAGE_PREFIX}{MAX_PAGE_HASH}{PAGE_SUFFIX}",
    ],
)
def test_the_allowlist_admits_every_name_this_writer_can_emit(name: str) -> None:
    """Every emittable filename is returned unchanged.

    The four overview names, all ten pinned feature pages, the reference tag
    page and the three boundary values of the hash segment -- ``-1``, ``0`` and
    ``4294967294``.  A name the writer can write and this function refuses is a
    page in the published tree that no page of that tree links.
    """
    assert pretty_reports.local_page_href(name) == name


@pytest.mark.parametrize(
    "candidate",
    [
        # Outside the offset hash's range at either end.
        f"{TAG_PAGE_PREFIX}-2{PAGE_SUFFIX}",
        f"{FEATURE_PAGE_PREFIX}-2{PAGE_SUFFIX}",
        f"{TAG_PAGE_PREFIX}{MAX_PAGE_HASH + 1}{PAGE_SUFFIX}",
        f"{TAG_PAGE_PREFIX}9999999999{PAGE_SUFFIX}",
        # A digit run far longer than any value the generator can produce.
        f"{TAG_PAGE_PREFIX}{'9' * 5000}{PAGE_SUFFIX}",
        # Spellings of a number that ``str`` never produces.
        f"{TAG_PAGE_PREFIX}-0{PAGE_SUFFIX}",
        f"{TAG_PAGE_PREFIX}+1{PAGE_SUFFIX}",
        f"{TAG_PAGE_PREFIX}01{PAGE_SUFFIX}",
        f"{TAG_PAGE_PREFIX}1.0{PAGE_SUFFIX}",
        f"{TAG_PAGE_PREFIX}--1{PAGE_SUFFIX}",
        # No hash segment at all.
        f"{FEATURE_PAGE_PREFIX}{PAGE_SUFFIX}",
        f"{TAG_PAGE_PREFIX}{PAGE_SUFFIX}",
        # Digits of another script, which Python's ``\d`` matches and no
        # filesystem entry of this tree carries.
        f"{TAG_PAGE_PREFIX}\u0661\u0662\u0663{PAGE_SUFFIX}",
        # Schemes, which autoescaping does not neutralise.
        "javascript:alert(9)",
        "data:text/html,<script>alert(9)</script>",
        "http://evil.example/x.html",
        "//evil.example/x.html",
        # Paths, absolute, parent-relative and nested.
        "/etc/passwd",
        "../../etc/passwd",
        f"../{SMOKE_TAG_PAGE}",
        f"pages/{SMOKE_TAG_PAGE}",
        f"{MIN_VALUE_FEATURE_PAGE}/../../x",
        # A query and a fragment, which no page of this tree takes.
        f"{OVERVIEW_INDEX}?a=b#z",
        f"{MIN_VALUE_TAG_PAGE}#top",
        # Surrounding whitespace and a control character.
        f" {MIN_VALUE_TAG_PAGE}",
        f"{MIN_VALUE_TAG_PAGE}\n",
        f"{TAG_PAGE_PREFIX}-1\x00{PAGE_SUFFIX}",
        # The wrong prefix and the wrong suffix.
        f"report-scenario_-1{PAGE_SUFFIX}",
        f"{TAG_PAGE_PREFIX}-1.htm",
        # Not a string, and empty.
        None,
        7,
        (),
        "",
    ],
)
def test_the_allowlist_refuses_anything_this_writer_cannot_emit(
    candidate: Any,
) -> None:
    """A destination the writer did not compute is answered with ``""``.

    The in-range negative value is the only signed segment there is, so every
    other signed or oddly spelled number is refused; so is every value above
    the maximum, including the ``4294967295`` a mask-based implementation of
    the hash would have produced and a digit run no integer of that range can
    have.  The template then renders the label as plain text, which is how a
    crafted destination in a result document reaches a reader as words rather
    than as an active link.
    """
    assert pretty_reports.local_page_href(candidate) == ""


def test_a_rejected_destination_is_logged_and_an_absent_one_is_not(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stripped link is a build-log line; an absent one is ordinary.

    The only way a rejected destination can arrive is a result document
    carrying a name the writer did not compute, which is worth reporting.  An
    empty or absent value is not: a feature with no URI has no page, and that
    is a state the writer reports in its own right.
    """
    with caplog.at_level("WARNING", logger=pretty_reports.logger.name):
        assert pretty_reports.local_page_href("javascript:alert(9)") == ""
    assert [record.getMessage() for record in caplog.records]

    caplog.clear()
    with caplog.at_level("WARNING", logger=pretty_reports.logger.name):
        assert pretty_reports.local_page_href("") == ""
        assert pretty_reports.local_page_href(None) == ""
        assert pretty_reports.local_page_href(MIN_VALUE_TAG_PAGE) == (
            MIN_VALUE_TAG_PAGE
        )
    assert [record.getMessage() for record in caplog.records] == []


def test_a_tag_whose_hash_is_the_minimum_value_is_linked_end_to_end(
    pretty_env: Environment, caplog: pytest.LogCaptureFixture
) -> None:
    """The negative page name survives a whole render, as a link.

    The round trip the two halves of the contract meet in: a tag whose name
    hashes to ``Integer.MIN_VALUE`` gets the page ``report-tag_-1.html``, and
    the tags overview links that page instead of rendering the tag as plain
    text.  A run of this suite reaches the case as soon as somebody writes a
    tag whose hash happens to land there, so the page and its link are asserted
    together -- the page existing while nothing links it is the defect, not the
    page name itself.
    """
    assert pretty_reports.java_hash_code(MIN_VALUE_TAG) == INT32_MIN
    document = build_document(
        build_feature(
            "Crm.feature",
            "Testinium app CRM Module",
            (build_element("a scenario", (build_step("acts", "passed", 4),)),),
            tags=(MIN_VALUE_TAG,),
        )
    )

    with caplog.at_level("WARNING", logger=pretty_reports.logger.name):
        pages = pretty_reports.render_pretty_pages(document, environment=pretty_env)

    assert pretty_reports.tag_page_name(MIN_VALUE_TAG) == MIN_VALUE_TAG_PAGE
    assert MIN_VALUE_TAG_PAGE in pages
    tags_overview = parse_page(OVERVIEW_PAGES[1], pages[OVERVIEW_PAGES[1]])
    assert MIN_VALUE_TAG_PAGE in resolvable_references(tags_overview)
    assert MIN_VALUE_TAG in tags_overview.normalized_text
    # The tag chip on the feature page is the other sink for the same name.
    feature_page = parse_page(
        SUITE_FEATURE_PAGES["Crm.feature"],
        pages[SUITE_FEATURE_PAGES["Crm.feature"]],
    )
    assert MIN_VALUE_TAG_PAGE in resolvable_references(feature_page)
    # Every page of the tree, so no sink anywhere stripped the destination.
    for name, page in parse_pages(pages).items():
        for value in resolvable_references(page):
            assert value != "", name
    assert [
        message
        for message in (record.getMessage() for record in caplog.records)
        if "Rejected" in message
    ] == []


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

    Rewritten for the descriptor-bound publication: the copy takes the
    publication that owns the destination rather than a destination path,
    because every file it writes has to be created under the staging
    descriptor the path authority verified.  The paths it reports are therefore
    inside the staging tree, which is where the assets are until the swap
    renames it, and that is what is asserted here.
    """
    with staged_publication(tmp_path / "assets-only") as publication:
        written = pretty_reports.copy_pretty_assets(publication)

        assert isinstance(written, tuple)
        assert len(written) == 22
        assert list(written) == sorted(written)
        assert all(path.is_absolute() for path in written)
        assert {
            path.relative_to(publication.staging).as_posix() for path in written
        } == EXPECTED_ASSETS
        assert asset_files(publication.staging) == EXPECTED_ASSETS


def test_copy_pretty_assets_creates_its_destination(tmp_path: Path) -> None:
    """A destination that does not exist yet is created, parents included.

    Rewritten for the descriptor-bound publication, which is now what creates
    the destination: beginning a publication creates and verifies the parent of
    the tree it will publish, and staging it creates the directory the assets
    are copied into -- so a copy into a location nothing has prepared still
    lands, and it lands under a descriptor rather than under a name.
    """
    final = tmp_path / "absent" / "deeper" / "tree"
    assert not final.parent.exists()

    with staged_publication(final) as publication:
        pretty_reports.copy_pretty_assets(publication)

        assert asset_files(publication.staging) == EXPECTED_ASSETS
        assert publication.staging.parent == final.parent


def test_copy_pretty_assets_is_idempotent_and_still_byte_exact(
    tmp_path: Path,
) -> None:
    """A second copy over the first leaves the same 22 files, same bytes.

    The asset copy runs on every render, so it has to be repeatable without
    accumulating, truncating or re-encoding anything.  Repeated within one
    publication here rather than into a bare directory twice: the writer's
    second render is a second publication, and what has to be idempotent is the
    copy into a staging tree that already holds a copy -- which is exactly what
    an interrupted publication of this process leaves for the next one to build
    over.
    """
    with staged_publication(tmp_path / "twice") as publication:
        first = pretty_reports.copy_pretty_assets(publication)
        before = tree_digests(publication.staging)
        second = pretty_reports.copy_pretty_assets(publication)
        after = tree_digests(publication.staging)

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
    monkeypatch.chdir(elsewhere)

    assert not (Path.cwd() / "app").exists()
    with staged_publication(tmp_path / "from-elsewhere") as publication:
        written = pretty_reports.copy_pretty_assets(publication)

        assert len(written) == 22
        assert asset_files(publication.staging) == EXPECTED_ASSETS
        for name in sorted(VENDORED_ASSETS):
            assert publication.staging.joinpath(*name.split("/")).read_bytes() == (
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

    # THE SAMPLE RUN'S UNDEFINED SCENARIO IS LISTED TOO, and that assertion is
    # new rather than relaxed.  ``net.masterthought:cucumber-reporting:5.6.1``
    # lists every element for which ``Status.isPassed()`` is false, and the
    # statistics tables of this very tree already counted this scenario as a
    # failed scenario -- its Sales row reads one failed scenario -- so while
    # the page selected on a literal ``failed`` token it contradicted every
    # other page of the artifact it belongs to.
    assert "Lucas" in text
    assert "User can search the customer from the search bar" in text
    assert ("undefined", "Undefined (1)") in filter_buttons(page)


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


# --------------------------------------------------------------------------
# The steps overview's occurrence rule
#
# ONE OCCURRENCE PER LOCATED STEP, whatever its result says.  The page used to
# drop a step whose status the port's vocabulary could not read, on the
# reasoning that a row cannot report a result it never understood -- and the
# reasoning does not survive the arithmetic beside it:
# ``app/reporting/aggregation.py``'s ``count_steps`` counts that same step
# towards ``steps_total``, which is what fills every statistics table in this
# tree, so the dropped occurrence made this page report fewer steps than the
# tables printed next to it.  One run, two readings, no way for a reader to
# tell which was wrong.
#
# The rule these tests hold the page to is therefore: a step with a
# ``match.location`` is an occurrence of that implementation, a step without
# one is not (there is no key to aggregate it under), and a status outside the
# seven the result model produces is REPORTED as ``unknown`` rather than
# dropped.  The Ratio cell carries that reading -- ``passed`` when every
# occurrence passed, ``unknown`` when none of them reported an outcome this
# port recognises, ``failed`` otherwise, the mixed row included -- and the
# footer, which is five readings of the rendered rows, follows.
# --------------------------------------------------------------------------

#: The four implementations of :func:`unreadable_status_document`, each named
#: for the shape of result its steps carry.
UNREADABLE_PASSED_LOCATION: Final[str] = "features.steps.synthetic_steps.step_passed"
UNREADABLE_STATUS_LOCATION: Final[str] = "features.steps.synthetic_steps.step_odd"
UNREADABLE_RESULTLESS_LOCATION: Final[str] = "features.steps.synthetic_steps.step_bare"
UNREADABLE_MIXED_LOCATION: Final[str] = "features.steps.synthetic_steps.step_mixed"


def unreadable_status_document() -> dict[str, Any]:
    """A one-scenario document whose steps reach every occurrence branch.

    Six steps over four implementations plus two the page must not aggregate:

    * one plainly passing step, so the unchanged branch is asserted in the
      same render as the changed ones;
    * one carrying :data:`UNRECOGNISED_STATUS`, a word from no vocabulary
      this port knows;
    * one carrying **no result mapping at all**, which is the other way a
      step arrives with nothing readable -- the ``result`` key is removed
      rather than blanked, because an absent mapping and an empty one take
      different paths through the template's guarded lookups;
    * one implementation used twice, once passing and once unreadable, which
      is the mixed row;
    * one step with a blank location and one with no ``match`` key at all,
      neither of which names an implementation to aggregate under.

    :returns: The document, in the shape ``app/reporting/events.py`` owns.
    """
    passing = build_step(
        "a step that passed",
        "passed",
        duration=1_000_000,
        line=10,
        location=UNREADABLE_PASSED_LOCATION,
    )
    unreadable = build_step(
        "a step carrying another tool's word",
        UNRECOGNISED_STATUS,
        duration=2_000_000,
        line=11,
        location=UNREADABLE_STATUS_LOCATION,
    )
    resultless = build_step(
        "a step carrying no result at all",
        "passed",
        line=12,
        location=UNREADABLE_RESULTLESS_LOCATION,
    )
    del resultless["result"]
    mixed_pass = build_step(
        "the half of the mixed row that passed",
        "passed",
        duration=3_000_000,
        line=13,
        location=UNREADABLE_MIXED_LOCATION,
    )
    mixed_unreadable = build_step(
        "the half of the mixed row nobody read",
        UNRECOGNISED_STATUS,
        duration=4_000_000,
        line=14,
        location=UNREADABLE_MIXED_LOCATION,
    )
    blank_location = build_step(
        "a step naming no implementation",
        UNRECOGNISED_STATUS,
        duration=5_000_000,
        line=15,
        location="   ",
    )
    no_match = build_step(
        "a step with no match at all",
        UNRECOGNISED_STATUS,
        duration=6_000_000,
        line=16,
        location=UNREADABLE_PASSED_LOCATION,
    )
    del no_match["match"]

    return build_document(
        build_feature(
            "Unreadable.feature",
            "Unreadable results",
            (
                build_element(
                    "every occurrence branch",
                    (
                        passing,
                        unreadable,
                        resultless,
                        mixed_pass,
                        mixed_unreadable,
                        blank_location,
                        no_match,
                    ),
                ),
            ),
        )
    )


def steps_overview_of(document: Any, environment: Environment) -> ParsedPage:
    """Render ``document`` and return its parsed steps overview.

    :param document: The merged result document.
    :param environment: The template environment to render through.
    :returns: The parsed ``overview-steps.html``.
    """
    pages = pretty_reports.render_pretty_pages(document, environment=environment)
    return parse_page(OVERVIEW_PAGES[2], pages[OVERVIEW_PAGES[2]])


def test_a_located_step_with_an_unreadable_result_is_still_an_occurrence(
    pretty_env: Environment,
) -> None:
    """An unreadable result is reported as ``unknown``, never dropped.

    Both shapes of "nothing readable" reach the page as a row of their own:
    a status outside the seven the model produces, and a step carrying no
    ``result`` mapping at all.  Each is one occurrence of its implementation
    and each renders the ``unknown`` token on its Ratio cell -- the neutral
    grey ``main.css`` gives ``.tqa-table td.unknown`` -- so the reading is
    visibly neither a pass nor a failure.  The percentage is ``0.00%``
    because no occurrence passed, which is the honest numerator.

    The passing row in the same render is the control: the branch this page
    was measured on is untouched.
    """
    page = steps_overview_of(unreadable_status_document(), pretty_env)
    rows = steps_body_rows(page)

    assert set(rows) == {
        UNREADABLE_PASSED_LOCATION,
        UNREADABLE_STATUS_LOCATION,
        UNREADABLE_RESULTLESS_LOCATION,
        UNREADABLE_MIXED_LOCATION,
    }
    for location in (UNREADABLE_STATUS_LOCATION, UNREADABLE_RESULTLESS_LOCATION):
        row = rows[location]
        assert row.texts[1] == "1", location
        assert row.last.status == "unknown", location
        assert "unknown" in row.last.classes, location
        assert row.last.text == "0.00%", location
    # The control row, and the vocabulary word itself never reaches the page.
    assert rows[UNREADABLE_PASSED_LOCATION].texts[1] == "1"
    assert rows[UNREADABLE_PASSED_LOCATION].last.status == "passed"
    assert rows[UNREADABLE_PASSED_LOCATION].last.text == "100.00%"
    assert UNRECOGNISED_STATUS not in page.html

    # A step with no duration renders zero rather than a blank cell or an
    # exception: the resultless row carries three 0.000 duration cells.
    assert rows[UNREADABLE_RESULTLESS_LOCATION].texts[2:5] == ("0.000",) * 3

    # A step naming no implementation is still excluded -- the one exclusion
    # the page keeps, and it is about the aggregation key, not the result.
    assert "a step naming no implementation" not in page.normalized_text
    assert "a step with no match at all" not in page.normalized_text


def test_a_row_mixing_a_pass_with_an_unreadable_result_reads_failed(
    pretty_env: Environment,
) -> None:
    """``unknown`` is the all-or-nothing reading, so a mixed row is not green.

    One implementation, two occurrences, one of them passing and one of them
    unreadable.  The row did not fully pass and part of it was never
    understood, so it reads ``failed`` at 50.00%: ``unknown`` is reserved for
    a row where *nothing* reported an outcome this port recognises, because
    the reading that overstates a run's health is the one that misleads.
    """
    page = steps_overview_of(unreadable_status_document(), pretty_env)
    row = steps_body_rows(page)[UNREADABLE_MIXED_LOCATION]

    assert row.texts[1] == "2"
    assert row.last.text == "50.00%"
    assert row.last.status == "failed"
    assert "failed" in row.last.classes
    assert "unknown" not in row.last.classes


def test_the_steps_overview_footer_counts_every_located_occurrence(
    pretty_env: Environment,
) -> None:
    """The footer is a reading of the rows, unknown occurrences included.

    Its first two cells are the number of distinct implementations and the
    total occurrences, and both now count what the rows carry: four rows and
    five occurrences, against the three and three the old status exclusion
    produced for this same document.  That total is what has to agree with
    the ``steps_total`` the statistics tables render, which is the
    disagreement the exclusion caused.
    """
    document = unreadable_status_document()
    page = steps_overview_of(document, pretty_env)
    rows = steps_body_rows(page)
    footer = steps_footer_row(page)

    located = sum(
        1
        for feature in document["features"]
        for element in feature["elements"]
        for step in element["steps"]
        if str((step.get("match") or {}).get("location", "")).strip()
    )
    assert located == 5

    assert footer.texts[0] == str(len(rows)) == "4"
    assert footer.texts[1] == str(located) == "5"
    assert footer.texts[1] == str(sum(int(row.texts[1]) for row in rows.values()))
    assert footer.texts[5] == "Totals"
    assert len(footer.cells) == 6


def test_the_step_stats_override_reports_its_own_unknown_occurrences(
    pretty_env: Environment,
) -> None:
    """The Python-side override carries the same three readings.

    ``step_stats`` is the documented override for a writer that would rather
    aggregate in Python, so its rows reach the same Ratio cell.  ``unknown``
    is optional there and defaults to zero: a row that never counted them --
    the third below -- renders exactly as it did before the key existed,
    which is what keeps the override backwards compatible.
    """
    template = pretty_env.get_template("pretty/overview_steps.html")
    page = parse_page(
        OVERVIEW_PAGES[2],
        template.render(
            step_stats=[
                {
                    "location": "steps.all_unknown",
                    "occurrences": 2,
                    "passed": 0,
                    "unknown": 2,
                },
                {
                    "location": "steps.mixed",
                    "occurrences": 2,
                    "passed": 1,
                    "unknown": 1,
                },
                {"location": "steps.no_key", "occurrences": 2, "passed": 2},
            ]
        ),
    )
    rows = steps_body_rows(page)

    assert rows["steps.all_unknown"].last.status == "unknown"
    assert rows["steps.mixed"].last.status == "failed"
    assert rows["steps.no_key"].last.status == "passed"
    assert steps_footer_row(page).texts[:2] == ("3", "6")


def test_the_well_formed_steps_overview_reports_no_unknown_occurrence(
    sample_result_set: Any, sample_tree: Path
) -> None:
    """The measured page is unchanged: every sample status is one of the seven.

    The occurrence rule only moves a step whose result this port cannot read,
    and the sample document carries none, so the page keeps the two readings
    the reference was measured with -- ``passed`` and ``failed`` -- its
    seventeen rows and a footer occurrence total equal to every located step
    of the run.
    """
    page = read_tree_pages(sample_tree)[OVERVIEW_PAGES[2]]
    rows = steps_body_rows(page)
    footer = steps_footer_row(page)

    assert len(rows) == 17
    for location, row in rows.items():
        assert row.last.status in {"passed", "failed"}, location
        assert "unknown" not in row.last.classes, location
    assert 'data-report-status="unknown"' not in page.html

    located = sum(
        1
        for feature in sample_result_set["features"]
        for element in feature["elements"]
        if element.get("selected") is not False
        for step in element.get("steps", ())
        if str((step.get("match") or {}).get("location", "")).strip()
    )
    assert footer.texts[0] == "17"
    assert footer.texts[1] == str(located)


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

    **Every level of the page reads it, and that assertion changed.** This
    test used to require exactly ONE ``Unknown`` badge, because the element
    tree folded statuses for itself over a seven-token precedence: the fold
    found nothing it recognised, fell through to its ``passed`` empty answer,
    and the page read ``[passed, passed, passed, unknown]`` from the feature
    brief down to the step -- a feature and a scenario reported as passes
    although nothing established either.  The Pretty templates now read
    :mod:`app.reporting.aggregation`, whose ``STATUS_PRECEDENCE`` ranks
    ``unknown`` between ``untested`` and ``passed`` precisely so that a status
    nobody established cannot be folded away, which is the one-model
    invariant AAP 0.3.4 and 0.4.2 state.  Four badges is therefore the
    contract, not a relaxation of it: the step, its Steps group, its scenario
    and its feature all report ``Unknown``.
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

    # The raw vocabulary word the document carried never reaches the page in
    # any form, and no level of the page reports a pass: the four briefs the
    # tree emits for a one-step scenario -- feature, element, Steps group and
    # step -- all read Unknown, and none of them reads Passed.
    assert "Unknown" in page.badge_labels()
    assert UNRECOGNISED_STATUS not in page.normalized_text
    assert UNRECOGNISED_STATUS not in page.html
    unknown_badges = [badge for badge in page.badges if badge.label == "Unknown"]
    assert len(unknown_badges) == 4
    assert {badge.status for badge in unknown_badges} == {"unknown"}
    assert "Passed" not in page.badge_labels()
    # The briefs are what paint the rows, and the measured defect was their
    # sequence reading [passed, passed, passed, unknown] from the feature down
    # to the step.  Every one of the four is the honest token now.
    assert brief_statuses(page) == ("unknown",) * 4
    assert container_statuses(page, "feature") == ("unknown",)
    assert container_statuses(page, "element") == ("unknown",)
    assert container_statuses(page, "steps") == ("unknown",)


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
# The normalized model is what these pages read
#
# ``app/reporting/aggregation.py`` is this port's single normalised result
# model, which AAP 0.3.4 requires of both HTML artifacts and the HTTP views
# ("Both HTML outputs and the HTTP views render over one normalized result
# model ... so no view contradicts an artifact") and 0.4.2 repeats as a
# cross-file invariant.  The templates under ``app/templates/pretty/`` used to
# grade a run for themselves beside it, and the two gradings disagreed in three
# measurable ways:
#
# * A scenario whose steps all passed and whose AFTER-HOOK FAILED rendered a
#   passed brief with a Passed badge on its feature page, on every tag page and
#   on the failures page, beside a statistics row that counted it as a failed
#   scenario.  The local fold read steps only;
#   ``Element.calculateElementStatus`` in ``net.masterthought:cucumber-
#   reporting:5.6.1`` folds ``stepsStatus`` with ``beforeStatus`` and
#   ``afterStatus``.
# * A local precedence tuple naming seven statuses answered ``passed`` for an
#   element whose only step carried a status the model never produced, so a
#   feature page read ``[passed, passed, passed, unknown]`` from the feature
#   brief down to the step.  The authority ranks ``unknown`` between
#   ``untested`` and ``passed`` for exactly that reason.
# * ``contains_failure`` selected the failures overview's rows by a literal
#   ``failed`` token, while 5.6.1 lists every element for which
#   ``Status.isPassed()`` is false -- so an undefined, pending, skipped,
#   ambiguous, untested or unknown scenario vanished from the page that exists
#   to show what did not pass, while every statistics table in the same tree
#   counted it as a failed scenario.
#
# The templates now read the decorated values ``decorate_feature`` records and,
# where a mapping carries none, call the authority through the ``model_``
# globals ``build_environment`` installs.  These tests assert that contract from
# both directions: the rendered pages for inputs that used to be graded wrongly,
# and a structural assertion over the template sources that no second
# implementation has come back.
# --------------------------------------------------------------------------

#: The templates of ``app/templates/pretty/`` that render the element tree, and
#: therefore the ones that answer a status, verdict or duration question.
ELEMENT_TREE_TEMPLATES: Final[tuple[str, ...]] = (
    "_element_tree.html",
    "feature.html",
    "overview_failures.html",
    "tag.html",
)

#: The globals :func:`app.reporting.pretty_reports.build_environment` installs,
#: paired with the authority's own object each one must BE rather than merely
#: agree with.  Identity is the assertion: a copy of a fold is what this whole
#: section exists to keep out of the render path.
MODEL_GLOBAL_IDENTITIES: Final[tuple[tuple[str, Any], ...]] = (
    ("model_element_status", aggregation.element_status),
    ("model_element_steps_status", aggregation.element_steps_status),
    ("model_element_verdict", aggregation.element_verdict),
    ("model_feature_status", aggregation.feature_status),
    ("model_feature_verdict", aggregation.feature_verdict),
    ("model_unit_status", aggregation.unit_status),
    ("model_unit_verdict", aggregation.unit_verdict),
    ("model_element_duration_ns", aggregation.element_duration_ns),
    ("model_status_token", aggregation.status_token),
    ("model_is_passed_token", aggregation.is_passed_token),
    ("model_worst_status", aggregation.worst_status),
    ("model_stats_row", aggregation.stats_row),
    ("model_status_precedence", aggregation.STATUS_PRECEDENCE),
    ("model_status_reading_order", aggregation.STATUS_READING_ORDER),
)

#: A Jinja comment, in either whitespace-control spelling.  Stripped before the
#: structural assertion, so the prose that DESCRIBES a deleted fold cannot be
#: mistaken for the fold.
JINJA_COMMENT: Final[re.Pattern[str]] = re.compile(r"\{#.*?#\}", re.S)

#: Two quoted status tokens side by side, which is what a precedence tuple or a
#: severity list looks like in a template.
ADJACENT_STATUS_LITERALS: Final[re.Pattern[str]] = re.compile(
    r"'(?:passed|failed|skipped|pending|undefined|untested|ambiguous|unknown)'"
    r"\s*,\s*"
    r"'(?:passed|failed|skipped|pending|undefined|untested|ambiguous|unknown)'"
)

#: A status compared against a literal token, which is how a template counted
#: statuses into columns for itself.
STATUS_COMPARISON: Final[re.Pattern[str]] = re.compile(
    r"[=!]=\s*'(?:passed|failed|skipped|pending|undefined|untested|ambiguous|unknown)'"
)

#: A status appended to a list, which is the signature of a fold loop.
STATUS_ACCUMULATION: Final[re.Pattern[str]] = re.compile(r"append\([^)]*status")

#: A duration accumulated into a running total.
DURATION_ACCUMULATION: Final[re.Pattern[str]] = re.compile(r"duration\w* = [^%]*\+")

#: One filter button of a status filter group, as its token and its label.
FILTER_BUTTON: Final[re.Pattern[str]] = re.compile(
    r'data-report-filter="([^"]+)"[^>]*>\s*([^<]*?)\s*</button>'
)


def pretty_template_code() -> dict[str, str]:
    """Return every template of the Pretty folder as comment-free source.

    Jinja comments are removed and whitespace collapsed, so the structural
    assertions below are about what the engine executes rather than about the
    prose recording what was deleted.

    :returns: Template filename to its executable source, whitespace
        collapsed.
    """
    folder = paths.templates_dir() / "pretty"
    sources = {
        path.name: re.sub(r"\s+", " ", JINJA_COMMENT.sub(" ", path.read_text(encoding="utf-8")))
        for path in sorted(folder.glob("*.html"))
    }
    assert sources, f"no template found under {folder}"
    return sources


def with_after_hook(
    element: dict[str, Any],
    status: str,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Return ``element`` carrying one after-hook result.

    The after-hook is where this port records a teardown's outcome and the
    failure screenshot, and it is the half of an element's status that the
    templates used to ignore.

    :param element: An element from :func:`build_element`.
    :param status: The hook's raw ``result.status``.
    :param error_message: The hook's failure text, omitted when ``None``.
    :returns: A copy carrying an ``after`` list of one entry.
    """
    result: dict[str, Any] = {"status": status}
    if error_message is not None:
        result["error_message"] = error_message
    hooked = dict(element)
    hooked["after"] = [{"match": {"location": "features/environment.py:1"}, "result": result}]
    return hooked


def hook_failure_document() -> dict[str, Any]:
    """A document whose only scenario passed every step and failed its teardown.

    One feature, tagged so that a tag page exists for the same scenario, and
    one scenario: two passing steps and a failed after-hook.  Every surface of
    the tree has to report that scenario as a failure, because the authority's
    verdict for it is not a pass and the statistics row beside it counts it as
    a failed scenario.

    :returns: The document.
    """
    scenario = build_element(
        HOOK_FAILURE_SCENARIO,
        (
            build_step("acts", "passed", duration=3_000_000_000),
            build_step("asserts", "passed", duration=1_500_000_000),
        ),
    )
    return build_document(
        build_feature(
            "HookFailure.feature",
            "Teardown failure",
            (with_after_hook(scenario, "failed", error_message="teardown boom"),),
            tags=(SMOKE_TAG,),
        )
    )


#: The scenario name :func:`hook_failure_document` carries, looked for on three
#: pages.
HOOK_FAILURE_SCENARIO: Final[str] = "a scenario whose teardown failed"

#: The whole statistics row :func:`hook_failure_document` produces on its
#: feature page, cell by cell in the table's own column order: two passed
#: steps, nothing in the other four columns, two steps in total, ZERO passed
#: and ONE FAILED scenario of one, 4.500 seconds of step time -- the hook's
#: own time deliberately excluded -- and the binary verdict.
HOOK_FAILURE_ROW: Final[str] = "Teardown failure 2 0 0 0 0 2 0 1 1 4.500 Failed"

#: The same figures on the tag page, under the tag's own key cell.
HOOK_FAILURE_TAG_ROW: Final[str] = f"{SMOKE_TAG} 2 0 0 0 0 2 0 1 1 4.500 Failed"

#: The statuses :func:`every_status_document` drives, in the document order it
#: writes them.  ``passed`` leads so that the one scenario which must NOT reach
#: the failures overview is in the same render as the seven that must.
EVERY_STATUS_SEQUENCE: Final[tuple[str, ...]] = (
    "passed",
    "failed",
    "undefined",
    "pending",
    "skipped",
    "untested",
    "ambiguous",
    UNRECOGNISED_STATUS,
)

#: What each of those scenarios reads as once the authority has graded it: the
#: unrecognised status folds to the ``unknown`` fallback and every other token
#: is its own.
EVERY_STATUS_TOKENS: Final[tuple[str, ...]] = tuple(
    "unknown" if status == UNRECOGNISED_STATUS else status
    for status in EVERY_STATUS_SEQUENCE
)


def scenario_name_for(status: str) -> str:
    """Return the scenario name :func:`every_status_document` gives ``status``."""
    return f"the {status} scenario"


def every_status_document() -> dict[str, Any]:
    """One feature, one scenario per status, each named for its status.

    The input the failures overview's selection rule is asserted against:
    every reading 5.6.1 calls not-passed, plus the one it calls passed.

    :returns: The document.
    """
    return build_document(
        build_feature(
            "EveryStatus.feature",
            "Every reading",
            tuple(
                build_element(
                    scenario_name_for(status),
                    (build_step(f"a {status} step", status, duration=1_000_000),),
                    element_id=f"every-status;{index}",
                    line=10 + index,
                )
                for index, status in enumerate(EVERY_STATUS_SEQUENCE)
            ),
            tags=(SMOKE_TAG,),
        )
    )


def background_failure_document() -> dict[str, Any]:
    """A document whose Background failed and whose scenario therefore skipped.

    The element shape is the measured one -- a Cucumber-JVM probe emits the
    Background's failed step and the scenario's own steps ``skipped`` -- so
    each brief keeps its own reading while the scenario UNIT reads failed,
    which is what selects it here, counts it in the statistics tables and puts
    it in the rerun manifest.

    :returns: The document.
    """
    return build_document(
        build_feature(
            "BackgroundFailure.feature",
            "Background failure",
            (
                build_element(
                    BACKGROUND_FAILURE_NAME,
                    (
                        build_step(
                            "logs in",
                            "failed",
                            duration=2_000_000_000,
                            error_message="setup boom",
                        ),
                    ),
                    element_type="background",
                    keyword="Background",
                ),
                build_element(
                    BACKGROUND_FAILURE_SCENARIO,
                    (build_step("acts", "skipped"),),
                ),
            ),
            tags=(SMOKE_TAG,),
        )
    )


#: The two element names :func:`background_failure_document` carries.
BACKGROUND_FAILURE_NAME: Final[str] = "the background that failed"
BACKGROUND_FAILURE_SCENARIO: Final[str] = "the scenario behind it"


def container_statuses(page: ParsedPage, class_token: str) -> tuple[str, ...]:
    """Return the status hook of every element-tree container of one kind.

    :param page: A parsed page.
    :param class_token: ``"feature"``, ``"element"`` or ``"steps"`` -- the bare
        generator class the container carries.
    :returns: One status per container, in document order.
    """
    statuses: list[str] = []
    for tag, attributes in page.elements:
        if tag != "div" or class_token not in attributes.get("class", "").split():
            continue
        for hook in STATUS_HOOK_ATTRIBUTES:
            if hook in attributes:
                statuses.append(attributes[hook])
                break
    return tuple(statuses)


def brief_statuses(page: ParsedPage) -> tuple[str, ...]:
    """Return the status class of every ``div.brief`` the page carries.

    The brief is what paints a row, so this is the colour a reader sees, read
    from the class the generator's own vocabulary puts it in.

    :param page: A parsed page.
    :returns: One status token per brief, in document order.
    """
    briefs: list[str] = []
    for tag, attributes in page.elements:
        classes = attributes.get("class", "").split()
        if tag != "div" or "brief" not in classes:
            continue
        tokens = [token for token in classes if token in STATUS_LABELS]
        assert len(tokens) == 1, f"{page.name}: brief carries {tokens}"
        briefs.append(tokens[0])
    return tuple(briefs)


def filter_buttons(page: ParsedPage) -> tuple[tuple[str, str], ...]:
    """Return the page's status filter buttons as ``(token, label)`` pairs.

    :param page: A parsed page.
    :returns: The buttons in document order, the reserved ``all`` control
        included.
    """
    return tuple(FILTER_BUTTON.findall(page.html))


def failures_overview_of(document: Any, environment: Environment) -> ParsedPage:
    """Render ``document`` and return its parsed failures overview.

    :param document: The merged result document.
    :param environment: The template environment to render through.
    :returns: The parsed ``overview-failures.html``.
    """
    pages = pretty_reports.render_pretty_pages(document, environment=environment)
    return parse_page(OVERVIEW_PAGES[3], pages[OVERVIEW_PAGES[3]])


def test_the_writer_registers_the_aggregation_authority_on_its_environment(
    pretty_env: Environment,
) -> None:
    """Every model global IS the authority's own object, not a copy of it.

    The templates answer a status, verdict, duration or statistics question by
    calling one of these, so identity is what makes "one normalized result
    model" checkable rather than hopeful: a second implementation installed
    here would satisfy every rendering assertion in this module while
    re-creating exactly the divergence they were written for.
    """
    assert set(pretty_reports.MODEL_GLOBALS) == {
        name for name, _ in MODEL_GLOBAL_IDENTITIES
    }
    for name, authority in MODEL_GLOBAL_IDENTITIES:
        assert pretty_reports.MODEL_GLOBALS[name] is authority, name
        assert pretty_env.globals[name] is authority, name
    # The link allowlist is still installed beside them, since both are what
    # make this environment the only one that can render the folder.
    assert pretty_env.globals["local_page_href"] is pretty_reports.local_page_href


def test_a_failed_after_hook_reports_its_scenario_failed_on_every_page(
    pretty_env: Environment,
) -> None:
    """A passing scenario with a failed teardown is a failure everywhere.

    Its feature page, its tag page and the failures overview all badge it
    failed, and the failures overview lists it, because the authority's
    element status folds both hook groups exactly as
    ``Element.calculateElementStatus`` does.  Its nested Steps group still
    reads ``passed``: that brief answers for the steps -- the generator's
    ``stepsStatus`` -- and the steps did pass, which is the distinction that
    makes the page report what happened rather than flattening it.
    """
    pages = pretty_reports.render_pretty_pages(
        hook_failure_document(), environment=pretty_env
    )
    feature_page = parse_page(
        "feature",
        pages[pretty_reports.feature_page_name(f"{FEATURE_URI_PREFIX}HookFailure.feature")],
    )
    tag_page = parse_page(SMOKE_TAG_PAGE, pages[SMOKE_TAG_PAGE])
    failures = parse_page(OVERVIEW_PAGES[3], pages[OVERVIEW_PAGES[3]])

    for page in (feature_page, tag_page, failures):
        assert container_statuses(page, "element") == ("failed",), page.name
        assert container_statuses(page, "steps") == ("passed",), page.name
        assert HOOK_FAILURE_SCENARIO in page.normalized_text, page.name

    # The feature reads failed too, and its brief order is
    # feature, element, steps, step, step, hook: the element is failed while
    # its steps are not, which is the whole of the hook's contribution, and
    # the failed hook's own brief closes the element -- the one node on the
    # page that accounts for the red scenario above the green steps.
    assert container_statuses(feature_page, "feature") == ("failed",)
    assert brief_statuses(feature_page) == (
        "failed",
        "failed",
        "passed",
        "passed",
        "passed",
        "failed",
    )

    # And the statistics row on the same page agrees, cell for cell: two
    # passing steps, no failed step, and ONE FAILED SCENARIO of one -- the
    # pair of numbers that used to stand beside a Passed badge.  The
    # duration is the steps' 4.5 seconds, with the hook's time excluded.
    assert HOOK_FAILURE_ROW in feature_page.normalized_text
    assert HOOK_FAILURE_TAG_ROW in tag_page.normalized_text


def test_the_failures_overview_lists_every_non_passed_scenario_unit(
    pretty_env: Environment,
) -> None:
    """``Status.isPassed()`` being false is the rule, not a ``failed`` token.

    Seven of the eight scenarios are not passes -- failed, undefined, pending,
    skipped, untested, ambiguous and a status the model never produced -- and
    every one of them belongs on the page that exists to show a reader what
    did not pass, because every statistics table in the same tree already
    counts it as a failed scenario (5.6.1's ``getFailedScenarios()`` is total
    minus passed).  The passed scenario is the control: it must not be listed.
    """
    page = failures_overview_of(every_status_document(), pretty_env)
    expected = tuple(token for token in EVERY_STATUS_TOKENS if token != "passed")

    assert len(expected) == 7
    assert container_statuses(page, "element") == expected
    for status in EVERY_STATUS_SEQUENCE:
        name = scenario_name_for(status)
        if status == "passed":
            assert name not in page.normalized_text
        else:
            assert name in page.normalized_text, name

    # The filter offers one button per status actually present, in the
    # authority's severity order and with 'unknown' named exactly once -- the
    # order used to be that tuple plus a hand-appended ('unknown',), which
    # named the token twice the moment the authority took it into the order.
    buttons = filter_buttons(page)
    assert buttons[0] == ("all", f"All ({len(expected)})")
    assert tuple(token for token, _ in buttons[1:]) == tuple(
        token for token in aggregation.STATUS_PRECEDENCE if token in expected
    )
    assert [token for token, _ in buttons].count("unknown") == 1
    # Each count equals the number of rows the script hides for that status.
    for token, label in buttons[1:]:
        assert label == f"{token.capitalize()} (1)", token


def test_a_background_only_failure_lists_both_rows_with_the_background_present(
    pretty_env: Environment,
) -> None:
    """The failure a reader came for has to be on the page.

    A Background-only failure is ``background=failed`` with the scenario's own
    steps ``skipped`` in ``target/cucumber.json``, and each brief keeps that
    reading -- the Pretty pages and the JSON artifact describe one run.  What
    the unit reading decides is SELECTION: the scenario is listed although its
    own steps carry no failure, and its Background occurrence is listed
    immediately above it, in model order, so the traceback is where the reader
    looking at the scenario can see it.
    """
    page = failures_overview_of(background_failure_document(), pretty_env)

    assert container_statuses(page, "element") == ("failed", "skipped")
    assert BACKGROUND_FAILURE_NAME in page.normalized_text
    assert BACKGROUND_FAILURE_SCENARIO in page.normalized_text
    assert page.normalized_text.index(BACKGROUND_FAILURE_NAME) < page.normalized_text.index(
        BACKGROUND_FAILURE_SCENARIO
    )
    assert "setup boom" in page.normalized_text
    # Two filterable rows, two statuses, and the counts add up to them.
    buttons = filter_buttons(page)
    assert buttons[0] == ("all", "All (2)")
    assert tuple(buttons[1:]) == (("failed", "Failed (1)"), ("skipped", "Skipped (1)"))


def test_an_element_renders_the_duration_the_authority_recorded(
    pretty_env: Environment,
) -> None:
    """The lead duration is the decorated ``duration_ns``, formatted once.

    The span is compared against the formatting macro applied to the
    authority's own recorded value, so the assertion covers the whole path --
    which duration is summed, and how it is rendered -- without holding a
    third implementation of either.  The failed after-hook of the document is
    what makes it load-bearing: a hook's duration is never a step's, so an
    element that ran 4.5 seconds of steps must not report the hook's time too.
    """
    document = hook_failure_document()
    run = aggregation.normalize_run(document)
    formatter = pretty_env.get_template("pretty/_macros.html").module
    pages = pretty_reports.render_pretty_pages(document, environment=pretty_env)
    feature_page = pages[
        pretty_reports.feature_page_name(f"{FEATURE_URI_PREFIX}HookFailure.feature")
    ]

    elements = [
        element
        for feature in run.features
        for element in feature["elements"]
    ]
    assert len(elements) == 1
    for element in elements:
        assert element["duration_ns"] == 4_500_000_000
        assert str(formatter.duration_span(element["duration_ns"])) in feature_page

    # The hook carries no duration key at all here; the assertion above is
    # what proves the element's own figure is the sum of its steps and
    # nothing else.
    assert str(formatter.duration_span(4_500_000_000)) in feature_page

    # And the READING is the decorated key rather than a sum the template
    # performs, which only an element whose two answers differ can show: the
    # macro is handed a mapping recording twelve seconds over a single
    # one-nanosecond step, and it renders the recorded figure.
    tree = pretty_env.get_template("pretty/_element_tree.html").module
    recorded = str(
        tree.element_block(
            {
                "type": SCENARIO_TYPE,
                "keyword": "Scenario",
                "name": "a decorated element",
                "description": "",
                "steps": [build_step("acts", "passed", duration=1)],
                "status": "passed",
                "steps_status": "passed",
                "duration_ns": 12_000_000_000,
            }
        )
    )
    assert str(formatter.duration_span(12_000_000_000)) in recorded
    assert str(formatter.duration_span(1)) not in recorded


def test_no_pretty_template_folds_a_status_or_sums_a_duration() -> None:
    """The structural half: no second implementation has come back.

    Every rendering assertion above would still pass if a template reproduced
    the authority's arithmetic correctly today, and that is exactly how the
    two came to disagree in the first place -- so the sources themselves are
    asserted.  Comments are stripped first, because the prose that records a
    deleted fold is not the fold.
    """
    sources = pretty_template_code()

    assert set(ELEMENT_TREE_TEMPLATES) <= set(sources)
    for name, code in sources.items():
        assert not ADJACENT_STATUS_LITERALS.search(code), f"{name}: status literal sequence"
        assert "STATUS_PRECEDENCE = (" not in code, f"{name}: local precedence tuple"
        assert "worst_token" not in code, f"{name}: local severity fold"
        # Appending to the authority's order is what duplicated 'unknown' once
        # that token joined it.
        assert "STATUS_PRECEDENCE +" not in code, f"{name}: appends to the authority's order"

    for name in ELEMENT_TREE_TEMPLATES:
        code = sources[name]
        assert not STATUS_COMPARISON.search(code), f"{name}: compares a status to a literal"
        assert not STATUS_ACCUMULATION.search(code), f"{name}: collects statuses to fold"
        assert not DURATION_ACCUMULATION.search(code), f"{name}: sums a duration"
        # And the positive half: each one asks the authority.
        assert "model_" in code, f"{name}: reads no model global"


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
# The writer publishes a validated staging tree by rename, so a second render
# replaces the published tree rather than overwriting parts of it: the page set
# on disk is exactly the second document's, and a detail page whose feature or
# tag has gone is gone with it.  The five assertions below hold the tree a
# reader can reach to that contract -- the pages that exist, their content,
# the asset census, every reference resolving, and nothing reachable that the
# current document did not produce.
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
    went away -- fails here as loudly as a page left over as a file.
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

    The pages are read from disk rather than taken from the expected set, so a
    reference that resolves only under an assumption about which pages the
    tree holds cannot pass here: whatever is published is opened, parsed and
    followed.
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
    """(d) Nothing stale is reachable from anywhere in the tree.

    The four overview pages are the navigation surface: what they link is
    where a reader starts, and every detail page they name is one the current
    document demanded.  The walk then widens to every page in the tree,
    because a link out of this generation would be a route into another one
    from whichever page carried it.
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

    # Every page in the tree, not only the four overviews: each one is a page
    # the current document produced, and each page destination it carries is
    # another of them.  A tree in which both hold cannot take a reader to a
    # page the current run did not write.
    for name, page in pages.items():
        assert name in second_pages, f"{name} is a page this document did not ask for"
        for value in resolvable_references(page):
            if value.endswith(PAGE_SUFFIX):
                assert value in second_pages, f"{name} links stale {value}"


def test_second_render_publishes_exactly_the_second_page_set(
    rerendered_tree: tuple[Path, dict[str, Any], frozenset[str], frozenset[str]],
) -> None:
    """(e) The published tree holds the second document's pages and no others.

    Set equality, in both directions at once: every page the second document
    demands is there, and nothing else is -- the three feature pages the
    smaller document no longer asks for are **absent**, not merely unlinked,
    because publication replaces the tree rather than writing over parts of
    it.

    Every page on disk is also required to be a complete, parseable document,
    which is the other half of "one complete generation or none": a published
    tree may not hold a truncated page, whichever run wrote it.
    """
    root, _document, first_pages, second_pages = rerendered_tree
    pages = read_tree_pages(root)
    on_disk = frozenset(pages)

    assert on_disk == second_pages
    assert not (first_pages - second_pages) & on_disk

    for name in sorted(on_disk):
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
# can occupy beforehand: ``_recover_interrupted_publication`` clears this
# process's own scratch names on the way in, and a directory standing where a
# published page belongs is now simply replaced by the swap.  The fault is
# therefore injected at the page write itself, which is no longer a builtin
# ``open`` on a path but ``ArtifactDirectoryPublication.open`` on a name
# relative to the verified staging descriptor.  ``PublicationProbe`` below
# wraps the real publication and stands in for
# ``begin_directory_publication``, the module global the writer resolves first,
# so the object under test is the genuine one for every step except the page
# whose write must fail -- where it raises a genuine ``IsADirectoryError``,
# which is the same errno the previous mechanism produced, at the same point in
# the loop.  It is also the only seam from which a *probe* can run inside a
# publication, which is what the staging-visibility and planted-link tests
# below need.  ``monkeypatch`` removes the stand-in when the test ends.
# --------------------------------------------------------------------------


class PublicationProbe:
    """A publication that watches the page writes, and can break one.

    Wraps a real :class:`app.utils.paths.ArtifactDirectoryPublication` and
    delegates everything to it, so the publication under test is the genuine
    one -- the same verified parent descriptor, the same staging descriptor,
    the same renames and the same scratch removal.  Only
    :meth:`open` is intercepted, which is where the writer writes a page, and
    that gives a test two things it can get nowhere else:

    * a **page write that fails** deterministically, mid-loop, with a real
      errno, for the two I/O-fault tests.  The asset copy goes through
      :meth:`~app.utils.paths.ArtifactDirectoryPublication.copy_in` on the
      wrapped object, so it reaches the real ``open`` directly and
      :attr:`attempts` counts page writes only; and
    * a **hook that runs inside the publication**, while the staging tree
      exists and before the swap, for the tests that assert what is reachable
      at that instant and for the one that plants a link inside staging.

    Installed over ``pretty_reports.begin_directory_publication`` -- the module
    global the writer resolves -- by :func:`probe_publication`.
    """

    def __init__(
        self,
        publication: paths.ArtifactDirectoryPublication,
        *,
        fail_on: str | None = None,
        before_write: Any = None,
    ) -> None:
        """Wrap ``publication``.

        :param publication: The real publication every call is delegated to.
        :param fail_on: Tree-relative name of the page whose write must raise,
            or ``None`` for a probe that only observes.
        :param before_write: Called as ``before_write(publication, name)``
            before each page write, for a test that needs to observe or
            disturb the staging tree from inside the publication.
        """
        self._publication = publication
        self.fail_on = fail_on
        self.before_write = before_write
        self.attempts = 0
        self.written: list[str] = []

    def __getattr__(self, name: str) -> Any:
        """Delegate every attribute this class does not define.

        :param name: The attribute name.
        :returns: The wrapped publication's attribute.
        """
        return getattr(self._publication, name)

    def __enter__(self) -> "PublicationProbe":
        """Enter the wrapped publication and return this probe."""
        self._publication.__enter__()
        return self

    def __exit__(self, *exception: object) -> None:
        """Release the wrapped publication's descriptors."""
        self._publication.__exit__(*exception)

    def open(self, relative_name: str, **kwargs: Any) -> Any:
        """Open a page in the staging tree, unless it is the one that fails.

        :param relative_name: Tree-relative name of the page.
        :param kwargs: Keyword arguments for the real method.
        :returns: Whatever the real method returns.
        :raises IsADirectoryError: When ``relative_name`` is :attr:`fail_on`.
            The errno is the real one, so the writer sees an ordinary
            :class:`OSError` and cannot distinguish this from a filesystem
            that genuinely refused the write.
        """
        self.attempts += 1
        if self.before_write is not None:
            self.before_write(self._publication, relative_name)
        if relative_name == self.fail_on:
            raise IsADirectoryError(
                errno.EISDIR,
                os.strerror(errno.EISDIR),
                str(self._publication.staging / relative_name),
            )
        self.written.append(relative_name)
        return self._publication.open(relative_name, **kwargs)


def probe_publication(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_on: str | None = None,
    before_write: Any = None,
) -> list[PublicationProbe]:
    """Make the writer publish through a :class:`PublicationProbe`.

    :param monkeypatch: pytest's patcher, which removes the stand-in again.
    :param fail_on: Passed to the probe.
    :param before_write: Passed to the probe.
    :returns: A list the probes are appended to, in creation order, so a test
        can assert on the one the call under test used.
    """
    probes: list[PublicationProbe] = []

    def begin(final: Any, **kwargs: Any) -> PublicationProbe:
        probe = PublicationProbe(
            paths.begin_directory_publication(final, **kwargs),
            fail_on=fail_on,
            before_write=before_write,
        )
        probes.append(probe)
        return probe

    monkeypatch.setattr(pretty_reports, "begin_directory_publication", begin)
    return probes


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
    a tree one page short of complete, which is the state a publication must
    not let out.  Nothing of it reaches the destination: the published
    directory does not exist, no page or asset file exists anywhere under the
    build output directory, and neither scratch directory is left behind.

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
    probes = probe_publication(monkeypatch, fail_on=SMOKE_TAG_PAGE)

    with pytest.raises(IsADirectoryError):
        pretty_reports.write_pretty_reports(
            sample_result_set, base=tmp_artifact_root, environment=pretty_env
        )

    # One open per page, so the fault really was reached at the end of a loop
    # that had already written every other page rather than at the start of it.
    # The asset copy does not appear in the count: it goes through the
    # publication's own copy_in, which reaches the real open directly.
    (fault,) = probes
    assert fault.attempts == len(expected)
    assert fault.written == [name for name in expected if name != SMOKE_TAG_PAGE]
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
    than a mixture of two runs.  The claim is made over the whole tree rather
    than page by page, which is what the artifact contract needs: a reader
    cannot tell which pages a failed run happened to reach.

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
    probe_publication(monkeypatch, fail_on=SMOKE_TAG_PAGE)

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

    with staged_publication(tmp_path / "broken-tree") as publication:
        with pytest.raises(FileNotFoundError) as failure:
            pretty_reports.copy_pretty_assets(publication)

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

    A font is requested from a stylesheet rather than from a page, and that
    makes no difference to the outcome: the stylesheet doing the requesting is
    a file this writer itself copies into the tree, so a missing font is the
    writer publishing a reference to something it knows is not there -- a
    dangling reference in a published artifact, which is the one thing an
    offline report cannot survive.  All eleven fonts are therefore as
    mandatory as the page-linked assets, and the failure names every asset
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

    with staged_publication(tmp_path / "fontless-tree") as publication:
        with pytest.raises(FileNotFoundError) as failure:
            pretty_reports.copy_pretty_assets(publication)
        staging = publication.staging

    message = str(failure.value)
    # The tree the check was made over is the staging tree, and the message
    # names it: the assets are verified where they were written, before
    # anything is renamed into place, which is why nothing dangling can reach
    # the published name.
    assert str(staging) in message
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
    assert not any(name.startswith("fonts/") for name in asset_files(staging))


# --------------------------------------------------------------------------
# Fan-out budgets
#
# The page set is a function of the result document: one page per feature, one
# per distinct tag, and every failure and screenshot repeated on each page that
# reaches it.  The document's own schema bounds one level at a time -- 1000
# features, 100 tags per level, 10,000 elements per feature, 250,000 nodes --
# which leaves room for a document that is small and entirely valid to ask for
# a tree orders of magnitude larger than itself.  ``MAX_PRETTY_TAGS``,
# ``MAX_PRETTY_DETAIL_PAGES`` and ``MAX_PRETTY_OUTPUT_BYTES`` bound that, and
# the writer refuses rather than truncating: a truncated report omits results
# silently, while a refusal is the exit contract's writer-failure class with
# the previously published tree left intact.
#
# The declared budgets are generous by design, so the tests that drive a
# refusal scale the budget down rather than scaling the document up: a document
# at the real tag cap renders a thousand pages and tens of megabytes, which is
# the amplification being bounded and not a fixture worth building.  The first
# test below is the one that holds the declared numbers themselves to this
# suite's own scale.
# --------------------------------------------------------------------------


def suite_tag_names() -> frozenset[str]:
    """Every tag this suite's feature files declare, read from disk.

    Tag lines are the lines whose first non-blank character is ``@``; a tag
    inside a step name or a data table is not a tag.  The directory comes from
    ``app.utils.paths`` rather than from a literal, as everywhere else here.

    :returns: The distinct tag names, leading ``@`` included.
    """
    names: set[str] = set()
    for path in sorted(paths.features_dir().glob("*.feature")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("@"):
                names.update(word for word in stripped.split() if word.startswith("@"))
    return frozenset(names)


def tagged_document(tag_count: int, feature_count: int = 1) -> dict[str, Any]:
    """Build a document carrying ``tag_count`` distinct tags in total.

    The tags are element-level and are spread over the features in turn, so the
    distinct-tag count and the feature count are independent -- which is what
    lets the tag budget and the detail-page budget be driven one at a time.

    :param tag_count: How many distinct tags the whole document carries.
    :param feature_count: How many features carry them, each with one scenario.
    :returns: The document, in the port's internal schema.
    """
    assert feature_count >= 1
    names = [f"@t{index:05d}" for index in range(tag_count)]
    features: list[dict[str, Any]] = []
    for index in range(feature_count):
        mine = names[index::feature_count]
        features.append(
            build_feature(
                f"Budget{index}.feature",
                f"Budget feature {index}",
                (
                    build_element(
                        f"scenario {index}",
                        (build_step("acts", "passed", duration=3),),
                        tags=tuple({"name": name} for name in mine),
                    ),
                ),
            )
        )
    return build_document(*features)


def padded_png_bytes(payload_bytes: int) -> bytes:
    """Return a structurally valid PNG carrying ``payload_bytes`` of padding.

    The size has to come from a chunk the format defines rather than from
    filler after the signature: ``app/reporting/screenshots.py`` walks every
    chunk, CRC-checks it and holds the image data to the size IHDR declares,
    so a signature followed by arbitrary bytes is discarded outright and would
    leave a "large screenshot" that renders as nothing at all.  The padding
    therefore rides in a ``tEXt`` chunk -- one of the safe, uncompressed
    ancillary types that contract admits -- spliced in front of the 1x1
    image's ``IEND``, whose own twelve bytes are the tail every well-formed
    PNG ends with.

    :param payload_bytes: How many bytes of padding to carry.
    :returns: The PNG's bytes.
    """
    image = base64.b64decode(VALID_PNG_PAYLOAD)
    assert image.startswith(PNG_SIGNATURE), "the base image is not a PNG"
    body = PNG_TEXT_KEYWORD + b"\x00" + b"a" * max(payload_bytes, 0)
    chunk = (
        len(body).to_bytes(PNG_CHUNK_FIELD_BYTES, "big")
        + PNG_TEXT_CHUNK
        + body
        + zlib.crc32(body, zlib.crc32(PNG_TEXT_CHUNK)).to_bytes(
            PNG_CHUNK_FIELD_BYTES, "big"
        )
    )
    return image[:-PNG_END_CHUNK_BYTES] + chunk + image[-PNG_END_CHUNK_BYTES:]


def screenshot_document(payload_bytes: int, tag_count: int = 2) -> dict[str, Any]:
    """Build a document whose one failing scenario carries a large screenshot.

    The payload is a genuinely valid PNG, by way of
    :func:`padded_png_bytes`, because the embedding contract drops anything
    that is not -- and a discarded attachment renders nothing, which would
    leave this fixture unable to reach the byte budget it exists to reach.
    One screenshot is then repeated on its feature page, on the failures
    overview and on every tag page its scenario's tags reach, which is the
    amplification a page count cannot see.

    :param payload_bytes: How many bytes of padding the screenshot carries.
    :param tag_count: How many tags the scenario carries, and therefore how
        many further copies of the payload the tree holds.
    :returns: The document, in the port's internal schema.
    """
    payload = base64.b64encode(padded_png_bytes(payload_bytes)).decode("ascii")
    element = build_element(
        "a failing scenario",
        (
            build_step(
                "acts",
                "failed",
                duration=3,
                error_message="AssertionError: the step failed",
            ),
        ),
        tags=tuple({"name": f"@s{index:03d}"} for index in range(tag_count)),
    )
    element["after"] = [
        {
            "result": {"status": "passed", "duration": 1},
            "embeddings": [
                {"mime_type": PNG_MIME_TYPE, "data": payload, "name": "a shot"}
            ],
        }
    ]
    return build_document(
        build_feature("Crm.feature", "Testinium app CRM Module", (element,))
    )


def test_this_suite_is_far_inside_every_declared_fan_out_budget(
    sample_tree: Path,
) -> None:
    """No legitimate run of this suite can reach a budget.

    The budgets exist to refuse amplification, so they must be nowhere near the
    artifact a real run publishes: this suite declares 18 tags across ten
    feature files and its published tree is a fraction of a megabyte, each of
    them orders of magnitude inside the corresponding budget.  The detail-page
    budget is also checked against its own derivation -- every feature the
    result schema admits (``MAX_FEATURES`` is 1000) plus every tag this writer
    admits -- so a change to one of the two numbers cannot silently leave them
    inconsistent.
    """
    tags = suite_tag_names()
    feature_files = set(SUITE_FEATURE_PAGES)
    published_bytes = sum(
        (sample_tree / name).stat().st_size for name in page_files(sample_tree)
    )

    assert len(tags) == 18
    assert len(feature_files) == 10
    assert len(tags) * 50 < pretty_reports.MAX_PRETTY_TAGS
    assert (len(tags) + len(feature_files)) * 50 < (
        pretty_reports.MAX_PRETTY_DETAIL_PAGES
    )
    assert published_bytes * 1000 < pretty_reports.MAX_PRETTY_OUTPUT_BYTES
    assert pretty_reports.MAX_PRETTY_DETAIL_PAGES == (
        1000 + pretty_reports.MAX_PRETTY_TAGS
    )


def test_a_document_at_both_count_budgets_still_publishes(
    tmp_artifact_root: Path, pretty_env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A document exactly at the caps is a document the writer publishes.

    The budgets refuse what is *over* them, so the boundary is asserted from
    the inside as well: three distinct tags against a cap of three, and five
    detail pages against a cap of five, produce the whole tree -- four
    overviews, two feature pages and three tag pages -- with every page on
    disk.
    """
    monkeypatch.setattr(pretty_reports, "MAX_PRETTY_TAGS", 3)
    monkeypatch.setattr(pretty_reports, "MAX_PRETTY_DETAIL_PAGES", 5)
    document = tagged_document(3, feature_count=2)

    root = pretty_reports.write_pretty_reports(
        document, base=tmp_artifact_root, environment=pretty_env
    )

    pages = page_files(root)
    assert len(pages) == len(OVERVIEW_PAGES) + 5
    assert frozenset(OVERVIEW_PAGES) < pages
    assert len([name for name in pages if name.startswith(TAG_PAGE_PREFIX)]) == 3
    assert len([name for name in pages if name.startswith(FEATURE_PAGE_PREFIX)]) == 2
    assert asset_files(root) == EXPECTED_ASSETS
    assert publication_scratch(root) == frozenset()


def test_one_tag_over_the_budget_is_refused_before_any_directory_exists(
    tmp_artifact_root: Path, pretty_env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The distinct-tag budget, refused with nothing created and nothing left.

    The count is known from the model alone, so the refusal happens before a
    staging directory exists: ``begin_directory_publication`` -- the one
    function this writer reaches the filesystem through, and the only thing
    that creates a directory for it -- is never called, no path under the
    checkout root is created, and the message names both the count and the
    budget so the build log says which document was refused and by how much.
    """
    created: list[Any] = []
    monkeypatch.setattr(pretty_reports, "MAX_PRETTY_TAGS", 3)
    monkeypatch.setattr(
        pretty_reports,
        "begin_directory_publication",
        lambda directory: created.append(directory),
    )
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    document = tagged_document(4)

    with pytest.raises(pretty_reports.PrettyReportBudgetError) as refusal:
        pretty_reports.write_pretty_reports(
            document, base=tmp_artifact_root, environment=pretty_env
        )

    message = str(refusal.value)
    assert "4 distinct tags" in message
    assert "more than the 3" in message
    assert "MAX_PRETTY_TAGS" in message
    assert created == []
    assert not final.exists()
    assert not paths.target_root(tmp_artifact_root).exists()
    assert publication_scratch(final) == frozenset()


def test_one_detail_page_over_the_budget_is_refused_before_any_directory_exists(
    tmp_artifact_root: Path, pretty_env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The detail-page budget, driven through features rather than tags.

    A document can stay inside the tag budget and still ask for an unbounded
    page set through its features, so the page budget is a separate limit and
    is asserted separately: three features and two tags are five detail pages
    against a cap of four.  The refusal is again free of filesystem effects.
    """
    created: list[Any] = []
    monkeypatch.setattr(pretty_reports, "MAX_PRETTY_TAGS", 1000)
    monkeypatch.setattr(pretty_reports, "MAX_PRETTY_DETAIL_PAGES", 4)
    monkeypatch.setattr(
        pretty_reports,
        "begin_directory_publication",
        lambda directory: created.append(directory),
    )
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    document = tagged_document(2, feature_count=3)

    with pytest.raises(pretty_reports.PrettyReportBudgetError) as refusal:
        pretty_reports.write_pretty_reports(
            document, base=tmp_artifact_root, environment=pretty_env
        )

    message = str(refusal.value)
    assert "5 detail pages" in message
    assert "more than the 4" in message
    assert "MAX_PRETTY_DETAIL_PAGES" in message
    assert created == []
    assert not final.exists()
    assert publication_scratch(final) == frozenset()


def test_the_count_budgets_are_refused_by_the_pure_render_too(
    pretty_env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``render_pretty_pages`` refuses the same documents, yielding nothing.

    The pure render is the other entry point, and a budget that only the
    writing half applied would leave a caller free to build the whole tree in
    memory.  The iterator raises on its first iteration, before a page is
    rendered, so no page of an over-budget document exists even transiently.
    """
    monkeypatch.setattr(pretty_reports, "MAX_PRETTY_TAGS", 2)
    document = tagged_document(3)

    with pytest.raises(pretty_reports.PrettyReportBudgetError):
        pretty_reports.render_pretty_pages(document, environment=pretty_env)

    pages = pretty_reports.iter_pretty_pages(document, environment=pretty_env)
    with pytest.raises(pretty_reports.PrettyReportBudgetError):
        next(pages)


def test_the_cumulative_byte_budget_refuses_before_the_filesystem_is_touched(
    tmp_artifact_root: Path, pretty_env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The amplification a page count cannot see: one screenshot, four copies.

    One 256 KiB screenshot is repeated on the failures overview, on its
    feature page and on both tag pages, taking a document of a few hundred
    kilobytes past a budget of half a megabyte.  A page's size is not knowable
    without rendering it, so the budget accumulates as the pages are produced
    -- but it is applied by the page iterator, which the writer drains
    **before** it derives a staging path, creates a directory, copies an asset
    or opens a page for writing.  So the refusal costs the filesystem nothing
    at all: this test asserts that by spying on the three functions that would
    have touched it, and none of them is reached.

    The same budget refuses the pure render, because both paths go through the
    one iterator: a caller cannot obtain an over-budget page set at all, by any
    route this module offers.
    """
    budget = 500_000
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    document = screenshot_document(256 * 1024)

    # Measured with the budget still at its real value, through the unbounded
    # inner generator, so the fixture is proven able to pass the budget
    # without the measurement itself being refused.
    page_names = [
        name
        for name, _html in pretty_reports._iter_rendered_pages(
            document, environment=pretty_env
        )
    ]
    produced = sum(
        len(html.encode("utf-8"))
        for _name, html in pretty_reports._iter_rendered_pages(
            document, environment=pretty_env
        )
    )
    assert produced > budget, "the document must be able to pass the budget"

    monkeypatch.setattr(pretty_reports, "MAX_PRETTY_OUTPUT_BYTES", budget)

    # The three doors to the filesystem, each recording instead of opening.
    # The publication is the first of them and the only route to the other
    # two: it verifies and holds the parent of the published tree, and the
    # staging directory, the asset copy and every page write are made relative
    # to the descriptor it hands back.
    touched: list[str] = []
    monkeypatch.setattr(
        pretty_reports,
        "begin_directory_publication",
        lambda *args, **kwargs: touched.append("begin_directory_publication"),
    )
    monkeypatch.setattr(
        pretty_reports,
        "copy_pretty_assets",
        lambda *args, **kwargs: touched.append("copy_pretty_assets"),
    )
    monkeypatch.setattr(
        pretty_reports,
        "open",
        lambda *args, **kwargs: touched.append("open"),
        raising=False,
    )

    with pytest.raises(pretty_reports.PrettyReportBudgetError) as refusal:
        pretty_reports.write_pretty_reports(
            document, base=tmp_artifact_root, environment=pretty_env
        )

    message = str(refusal.value)
    assert f"more than the {budget}" in message
    assert "MAX_PRETTY_OUTPUT_BYTES" in message
    # The page the budget was reached at is named, so the log says where the
    # tree grew rather than only that it did.
    assert any(f"at {name}," in message for name in page_names), message

    assert touched == [], f"the refusal reached the filesystem: {touched}"
    assert not final.exists()
    assert publication_scratch(final) == frozenset()
    assert not paths.target_root(tmp_artifact_root).exists()

    # The pure render is refused by the same budget, through the same iterator.
    with pytest.raises(pretty_reports.PrettyReportBudgetError):
        pretty_reports.render_pretty_pages(document, environment=pretty_env)
    with pytest.raises(pretty_reports.PrettyReportBudgetError):
        list(pretty_reports.iter_pretty_pages(document, environment=pretty_env))


def test_the_byte_budget_leaves_a_previous_complete_tree_byte_for_byte(
    sample_result_set: Any,
    tmp_artifact_root: Path,
    pretty_env: Environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused publication does not degrade the report an earlier one wrote.

    The budget is checked before the writer touches the filesystem, so a
    refusal cannot reach the published tree even in principle.  This is the
    guarantee the exit contract needs stated over the artifact a reader opens:
    the previous complete generation is still there, byte for byte, rather
    than a mixture of it and a run that was refused.
    """
    root = pretty_reports.write_pretty_reports(
        sample_result_set, base=tmp_artifact_root, environment=pretty_env
    )
    before = tree_digests(root)

    monkeypatch.setattr(pretty_reports, "MAX_PRETTY_OUTPUT_BYTES", 1)
    with pytest.raises(pretty_reports.PrettyReportBudgetError):
        pretty_reports.write_pretty_reports(
            screenshot_document(1024), base=tmp_artifact_root, environment=pretty_env
        )

    assert tree_digests(root) == before
    assert publication_scratch(root) == frozenset()


def test_no_count_budget_can_suppress_the_four_overview_pages(
    tmp_artifact_root: Path, pretty_env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exit contract's four artifacts are not detail pages and are not capped.

    A run that selected nothing still owes a reader all four overview pages, so
    the count budgets are about detail pages only: with both of them at zero, an
    empty document publishes exactly the four overviews and the whole asset set.
    """
    monkeypatch.setattr(pretty_reports, "MAX_PRETTY_TAGS", 0)
    monkeypatch.setattr(pretty_reports, "MAX_PRETTY_DETAIL_PAGES", 0)

    empty: dict[str, Any] = {"features": []}
    pages = pretty_reports.render_pretty_pages(empty, environment=pretty_env)
    root = pretty_reports.write_pretty_reports(
        empty, base=tmp_artifact_root, environment=pretty_env
    )

    assert tuple(pages) == OVERVIEW_PAGES
    assert page_files(root) == frozenset(OVERVIEW_PAGES)
    assert asset_files(root) == EXPECTED_ASSETS



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
    # A genuinely well-formed 1x1 PNG, not a signature with filler behind it:
    # the lightbox partial renders nothing for a payload the inline-PNG
    # contract in app/reporting/screenshots.py rejects, and the positive
    # assertion at the end of this test would then pass vacuously.
    payload = VALID_PNG_PAYLOAD
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


# --------------------------------------------------------------------------
# The publication's write authority
#
# The section the descriptor-bound publication added.  Everything above reads
# the artifact; these read *how it came to be there*, because the two faults
# this change closed are invisible in a finished tree:
#
# * a page, an asset or a whole cleanup resolved from a pathname after the
#   check that approved it -- which let a link swapped in between redirect the
#   write, and let a path-resolved scratch removal delete a prepared directory
#   outside the artifact root; and
# * generated output inheriting the process umask, so a report carrying a
#   run's failure text and screenshots arrived group- and world-readable.
#
# Nothing here uses a permission trick to drive a failure -- this suite runs as
# root in a container, where they do not work -- so each hostile case is a
# link, a planted scratch directory or an injected errno.
# --------------------------------------------------------------------------


def test_every_published_file_and_directory_is_owner_only(
    sample_tree: Path,
) -> None:
    """The whole tree is the owner's to read: ``0700`` dirs, ``0600`` files.

    A page of this artifact carries the run's failure text, its step
    arguments and its embedded screenshots of the application under test, and
    the tree is written into a shared build agent's workspace.  Owner-only is
    therefore the policy the path authority applies to everything it creates
    (CWE-732/CWE-359), and this asserts it over every entry of a published
    tree rather than over a sample: the four asset directories, all 22 assets
    and all nine pages.

    The sources are ``0644`` in the checkout, which is exactly why the asset
    copy must not carry a source mode across -- see the test below, which
    forces that case rather than relying on the checkout's own modes.
    """
    permissions = tree_permissions(sample_tree)

    files = {
        name: mode
        for name, mode in permissions.items()
        if sample_tree.joinpath(*name.split("/")).is_file()
    }
    directories = {
        name: mode for name, mode in permissions.items() if name not in files
    }

    assert len(files) == len(SAMPLE_PAGES) + len(EXPECTED_ASSETS)
    assert set(directories) == set(ASSET_SUBDIRECTORIES)
    assert {mode for mode in files.values()} == {paths.ARTIFACT_FILE_MODE}
    assert {mode for mode in directories.values()} == {paths.ARTIFACT_DIR_MODE}
    # Stated a second way, against the mask the policy is defined by, so the
    # claim survives a change to either constant: nothing in the tree grants
    # the group or others anything at all.
    for name, mode in permissions.items():
        assert not mode & paths.ARTIFACT_MODE_MASK, (name, oct(mode))


def test_the_published_tree_and_its_owned_parents_are_owner_only(
    sample_tree: Path,
) -> None:
    """``target/``, ``target/cucumber/`` and the tree itself grant no one else.

    The directories above the tree are created by the publication on the way
    in, from the ``target`` component inward, so they are part of the same
    policy -- a ``0755`` build output directory an earlier run or the operator
    left behind is tightened through its own descriptor rather than accepted.
    """
    tree_parent = sample_tree.parent
    build_output = tree_parent.parent

    assert build_output.name == BUILD_OUTPUT_DIR_NAME
    assert tree_parent.name == TREE_PARENT_DIR_NAME
    for directory in (build_output, tree_parent, sample_tree):
        assert permission_bits(directory) == paths.ARTIFACT_DIR_MODE, directory


def test_a_group_readable_vendored_asset_is_published_owner_only(
    tmp_path: Path,
    tmp_artifact_root: Path,
    pretty_env: Environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``0644`` asset in the wheel does not arrive ``0644`` in the tree.

    The regression this change exists to prevent, forced rather than observed:
    the copy used to be :func:`shutil.copy2`, which copies the source's mode
    along with its bytes, so every vendored file landed with whatever
    permissions the installed package carried -- ``0644`` for a wheel, and
    group- and world-readable in the published report.  The copy now takes the
    bytes only and the destination is created ``0600`` like any page beside it.

    Driven over a vendor directory whose files are deliberately ``0644`` and
    ``0666``, so the source modes are the test's own rather than the
    checkout's, and the sources are asserted unchanged afterwards: the copy
    reads them and must not alter them either.
    """
    vendor = tmp_path / "group-readable-vendor"
    shutil.copytree(paths.vendor_dir(), vendor)
    sources = sorted(path for path in vendor.rglob("*") if path.is_file())
    assert len(sources) == len(VENDORED_ASSETS)
    for index, source in enumerate(sources):
        source.chmod(0o666 if index == 0 else 0o644)
    monkeypatch.setattr(pretty_reports, "vendor_dir", lambda: vendor)

    root = pretty_reports.write_pretty_reports(
        None, base=tmp_artifact_root, environment=pretty_env
    )

    assert asset_files(root) == EXPECTED_ASSETS
    for name in sorted(EXPECTED_ASSETS):
        copied = root.joinpath(*name.split("/"))
        assert permission_bits(copied) == paths.ARTIFACT_FILE_MODE, name
    # The bytes still travelled, which is the other half of the contract.
    for name in sorted(VENDORED_ASSETS):
        assert root.joinpath(*name.split("/")).read_bytes() == (
            vendor.joinpath(*name.split("/")).read_bytes()
        )
    # And the package's own files were only read.
    assert permission_bits(sources[0]) == 0o666
    assert {permission_bits(source) for source in sources[1:]} == {0o644}


def test_the_staging_tree_is_dot_prefixed_and_unreachable_while_it_exists(
    sample_result_set: Any,
    tmp_artifact_root: Path,
    pretty_env: Environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No partial generation is addressable, and none survives the call.

    Asserted from *inside* the publication, at the first page write, which is
    the only instant at which a partial tree exists at all: the staging
    directory's name begins with a dot, which is what
    :func:`app.utils.paths.resolve_artifact` rejects outright, so a page in it
    cannot be requested over HTTP even while it is on disk -- and the published
    name holds nothing yet, so a reader sees the viewer's ordinary 404 rather
    than half a report.

    After the swap the same request resolves to the overview page, and no
    scratch directory is left beside the tree.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    observed: list[tuple[str, bool, object, object]] = []

    def observe(publication: Any, relative_name: str) -> None:
        observed.append(
            (
                publication.staging.name,
                final.exists(),
                paths.resolve_artifact(
                    f"{TREE_PARENT_DIR_NAME}/{publication.staging.name}/"
                    f"{OVERVIEW_INDEX}",
                    tmp_artifact_root,
                ),
                paths.resolve_artifact(TREE_PARENT_DIR_NAME, tmp_artifact_root),
            )
        )

    probe_publication(monkeypatch, before_write=observe)

    root = pretty_reports.write_pretty_reports(
        sample_result_set, base=tmp_artifact_root, environment=pretty_env
    )

    assert len(observed) == len(SAMPLE_PAGES)
    staging_name, published_existed, staged_request, directory_request = observed[0]
    assert staging_name.startswith(".")
    assert paths.PUBLICATION_STAGING_INFIX in staging_name
    assert not published_existed
    assert staged_request is None
    assert directory_request is None

    assert paths.resolve_artifact(TREE_PARENT_DIR_NAME, tmp_artifact_root) == (
        paths.pretty_reports_index_path(tmp_artifact_root).resolve()
    )
    assert publication_scratch(root) == frozenset()
    assert page_files(root) == frozenset(SAMPLE_PAGES)


def test_no_publication_scratch_survives_a_successful_publication(
    sample_tree: Path,
) -> None:
    """The parent of a published tree holds the tree and nothing else.

    The staging directory and the renamed-aside copy are both removed by the
    call that made them, so a successful run leaves no dot-directory in the
    build output for ``--clean`` to have to sweep.
    """
    assert publication_scratch(sample_tree) == frozenset()
    assert listed_names(sample_tree.parent) == frozenset({sample_tree.name})


def test_a_missing_asset_fault_leaves_the_previous_tree_and_no_scratch(
    sample_result_set: Any,
    tmp_path: Path,
    tmp_artifact_root: Path,
    pretty_env: Environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The asset check fails the publication, not the published tree.

    The third fault the writer must survive with a tree already on disk: a
    damaged installation, driven by pointing the vendor accessor at a directory
    holding one file.  The fault is raised inside the staging tree before
    anything is renamed, so the previous generation is still byte-for-byte
    itself and no scratch is left behind.
    """
    root = pretty_reports.write_pretty_reports(
        sample_result_set, base=tmp_artifact_root, environment=pretty_env
    )
    before = tree_digests(root)
    incomplete = tmp_path / "incomplete-vendor"
    (incomplete / "css").mkdir(parents=True)
    (incomplete / "css" / "cucumber.css").write_bytes(b"/* partial install */\n")
    monkeypatch.setattr(pretty_reports, "vendor_dir", lambda: incomplete)

    with pytest.raises(FileNotFoundError):
        pretty_reports.write_pretty_reports(
            sample_result_set, base=tmp_artifact_root, environment=pretty_env
        )

    assert tree_digests(root) == before
    assert publication_scratch(root) == frozenset()


def test_a_fault_after_the_first_rename_restores_the_renamed_aside_tree(
    sample_result_set: Any,
    tmp_artifact_root: Path,
    pretty_env: Environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one instant the swap cannot remove is survivable, and survived.

    The swap is two renames, and this occupies the gap between them: the
    previous generation has been renamed aside and the staging tree fails to
    take its place.  The renamed-aside copy is then **the only complete
    generation in existence**, so the writer renames it back before it reports
    anything, and what a reader finds afterwards is the earlier tree in full
    with no scratch beside it.

    Driven by making the publication's second rename fail.  That instant cannot
    be reached from outside the process any other way -- there is no public
    call that stops between the two renames -- so the rename helper is the seam
    the fault is injected at, with the first rename left to do its real work so
    the state under test is the real one.
    """
    root = pretty_reports.write_pretty_reports(
        sample_result_set, base=tmp_artifact_root, environment=pretty_env
    )
    before = tree_digests(root)
    original = paths.ArtifactDirectoryPublication._rename_scratch

    def failing_rename(self: Any, source: str, destination: str) -> None:
        if source == self.staging.name:
            raise OSError(errno.EXDEV, os.strerror(errno.EXDEV), source)
        original(self, source, destination)

    monkeypatch.setattr(
        paths.ArtifactDirectoryPublication, "_rename_scratch", failing_rename
    )

    with pytest.raises(OSError) as failure:
        pretty_reports.write_pretty_reports(
            sample_result_set, base=tmp_artifact_root, environment=pretty_env
        )

    assert failure.value.errno == errno.EXDEV
    assert tree_digests(root) == before
    assert publication_scratch(root) == frozenset()


def refuse_the_asset_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the publication fail at its asset copy.

    The seam a "this publication then failed" case is injected at.  It has to
    be a step *inside* the publication, because the fan-out budgets and the
    whole render are settled before one begins -- that being what keeps an
    over-budget document free of filesystem effects -- so a render fault never
    reaches the recovery step under test.  The asset copy is the first step
    after it, and the exception is the one
    :func:`app.reporting.pretty_reports.copy_pretty_assets` really raises for
    an asset that is not there.

    :param monkeypatch: The active patcher.
    """

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        """Raise as a missing vendored asset does."""
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), "main.css")

    monkeypatch.setattr(pretty_reports, "copy_pretty_assets", refuse)


def test_an_orphaned_renamed_aside_tree_is_restored_and_never_removed(
    sample_result_set: Any,
    tmp_artifact_root: Path,
    pretty_env: Environment,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A publication killed between the two renames is put back together.

    The state a killed process leaves is built by hand -- a renamed-aside tree
    under the scratch name a publication of this tree recognises, and no
    published tree at all -- and then a publication is started that cannot get
    past its asset copy.  The restore happens **first**, before anything else
    in the call touches the filesystem, so the tree is published again even
    though this run then fails: a reader gets the previous generation rather
    than nothing.  It is renamed, never removed, which is the rule that makes
    the interrupted-publication window recoverable at all.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    aside = plant_scratch(
        final,
        scratch_name(final, paths.PUBLICATION_SUPERSEDED_INFIX, os.getpid()),
    )
    assert not final.exists()
    refuse_the_asset_copy(monkeypatch)

    with caplog.at_level("WARNING", logger=pretty_reports.logger.name):
        with pytest.raises(FileNotFoundError):
            pretty_reports.write_pretty_reports(
                sample_result_set,
                base=tmp_artifact_root,
                environment=pretty_env,
            )

    assert final.is_dir()
    assert tree_files(final) == frozenset({"marker.txt"})
    assert (final / "marker.txt").read_text(encoding="utf-8") == aside.name
    assert not aside.exists()
    assert publication_scratch(final) == frozenset()
    assert any(
        "Restored" in record.getMessage() for record in caplog.records
    ), [record.getMessage() for record in caplog.records]


def test_the_newest_renamed_aside_tree_is_the_one_restored(
    sample_result_set: Any,
    tmp_artifact_root: Path,
    pretty_env: Environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two candidates, and the choice is deterministic: newest, then name.

    Two interrupted publications can each leave a renamed-aside copy, and the
    later one is the later generation of the report.  A copy carrying another
    process's identifier is a candidate too -- it is a complete generation of
    this artifact whoever produced it, and the alternative is publishing
    nothing where a report exists -- while *staging* scratch of another process
    is not, which is what the test below covers.

    The copy that loses is this process's own, so it is also cleared by the
    same call, and the one that wins is left published.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    older = plant_scratch(
        final,
        scratch_name(final, paths.PUBLICATION_SUPERSEDED_INFIX, os.getpid()),
    )
    newer = plant_scratch(
        final,
        scratch_name(final, paths.PUBLICATION_SUPERSEDED_INFIX, FOREIGN_PID),
    )
    os.utime(older, (1_600_000_000, 1_600_000_000))
    os.utime(newer, (1_700_000_000, 1_700_000_000))
    refuse_the_asset_copy(monkeypatch)

    with pytest.raises(FileNotFoundError):
        pretty_reports.write_pretty_reports(
            sample_result_set,
            base=tmp_artifact_root,
            environment=pretty_env,
        )

    assert (final / "marker.txt").read_text(encoding="utf-8") == newer.name
    assert not newer.exists()
    assert not older.exists()
    assert publication_scratch(final) == frozenset()


def test_another_process_s_scratch_is_left_exactly_where_it_is(
    sample_result_set: Any,
    tmp_artifact_root: Path,
    pretty_env: Environment,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Staging scratch carrying another process's id is reported, not swept.

    Removing it would be the one way this writer could destroy a concurrent
    publication's work, and no publisher can tell a dead process's leavings
    from a live one's portably.  So it is logged and left: it is dot-prefixed,
    so no request can reach it, and ``app/cli.py``'s ``--clean`` empties the
    build output on the next ordinary run.

    The publication around it completes normally, which is the other half of
    the claim: another process's scratch neither blocks this one nor appears
    in its output.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    foreign = plant_scratch(
        final, scratch_name(final, paths.PUBLICATION_STAGING_INFIX, FOREIGN_PID)
    )

    with caplog.at_level("WARNING", logger=pretty_reports.logger.name):
        root = pretty_reports.write_pretty_reports(
            sample_result_set, base=tmp_artifact_root, environment=pretty_env
        )

    assert foreign.is_dir()
    assert (foreign / "marker.txt").read_text(encoding="utf-8") == foreign.name
    assert page_files(root) == frozenset(SAMPLE_PAGES)
    assert asset_files(root) == EXPECTED_ASSETS
    assert publication_scratch(root) == frozenset({foreign.name})
    assert any(
        "Leaving" in record.getMessage() and foreign.name in record.getMessage()
        for record in caplog.records
    ), [record.getMessage() for record in caplog.records]


def test_this_process_s_own_stale_scratch_is_cleared(
    sample_result_set: Any,
    tmp_artifact_root: Path,
    pretty_env: Environment,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A previous run of this process cannot still own its scratch names.

    So they are removed on the way in, rather than built over: a staging tree
    left half-written by an earlier run of this process would otherwise
    contribute its files to this publication's inventory, and the inventory is
    what the swap is authorised by.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    own = plant_scratch(
        final, scratch_name(final, paths.PUBLICATION_STAGING_INFIX, os.getpid())
    )
    (own / "css").mkdir()
    (own / "css" / "leftover.css").write_text("/* half a run */\n", encoding="utf-8")

    with caplog.at_level("WARNING", logger=pretty_reports.logger.name):
        root = pretty_reports.write_pretty_reports(
            sample_result_set, base=tmp_artifact_root, environment=pretty_env
        )

    assert not own.exists()
    assert page_files(root) == frozenset(SAMPLE_PAGES)
    assert asset_files(root) == EXPECTED_ASSETS
    assert "leftover.css" not in tree_files(root)
    assert publication_scratch(root) == frozenset()
    assert any(
        "Removing" in record.getMessage() and own.name in record.getMessage()
        for record in caplog.records
    ), [record.getMessage() for record in caplog.records]


def test_a_link_inside_cleared_scratch_is_unlinked_not_followed(
    sample_result_set: Any,
    tmp_path: Path,
    tmp_artifact_root: Path,
    pretty_env: Environment,
) -> None:
    """A link inside a scratch tree is unlinked, never descended.

    The publication's removal is descriptor-relative and no-follow at every
    step: a directory is entered only through a descriptor opened on the entry
    itself, so a symbolic link -- or, on Windows, a junction, which
    ``Path.is_dir`` follows and a directory walk treats as a directory --
    planted inside the scratch is unlinked rather than turned into a recursive
    delete of whatever it addresses (CWE-59/CWE-22).

    Both shapes are planted, a link to a directory and a link to a file, inside
    this process's own stale scratch, which the publication clears on the way
    in.  The scratch goes; the prepared directory outside the artifact root,
    its file and its nested directory are all exactly as they were.

    This pins the contract; the *deletion* the review's probe achieved came
    through a swapped path **component** rather than through a planted link,
    and :func:`test_a_symlinked_build_output_component_is_refused` below
    reproduces that one.
    """
    outside = tmp_path / "prepared-elsewhere"
    outside.mkdir()
    (outside / "keep.txt").write_text("not the writer's to delete\n", encoding="utf-8")
    (outside / "nested").mkdir()
    (outside / "nested" / "deeper.txt").write_text("also not\n", encoding="utf-8")
    before = tree_digests(outside)

    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    own = plant_scratch(
        final, scratch_name(final, paths.PUBLICATION_STAGING_INFIX, os.getpid())
    )
    (own / "escape-dir").symlink_to(outside, target_is_directory=True)
    (own / "escape-file").symlink_to(outside / "keep.txt")

    root = pretty_reports.write_pretty_reports(
        sample_result_set, base=tmp_artifact_root, environment=pretty_env
    )

    assert not own.exists()
    assert outside.is_dir()
    assert tree_digests(outside) == before
    assert listed_names(outside) == frozenset({"keep.txt", "nested"})
    assert page_files(root) == frozenset(SAMPLE_PAGES)
    assert publication_scratch(root) == frozenset()


def test_a_link_planted_in_staging_mid_publication_is_unlinked_not_followed(
    sample_result_set: Any,
    tmp_path: Path,
    tmp_artifact_root: Path,
    pretty_env: Environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same probe against the failure path's own cleanup.

    The staging tree is removed on every failure, and a failing publication is
    precisely when something may already have been planted inside it.  The link
    is created from inside the publication, at the last page write, and that
    same write then fails -- so the cleanup that runs is the failure path's,
    with a link sitting in the tree it is about to remove.

    Nothing outside is touched, no scratch survives, and the fault itself still
    propagates: the run's exit table needs the writer failure reported.
    """
    outside = tmp_path / "prepared-elsewhere"
    outside.mkdir()
    (outside / "keep.txt").write_text("not the writer's to delete\n", encoding="utf-8")
    before = tree_digests(outside)
    final = paths.pretty_reports_html_dir(tmp_artifact_root)

    def plant_link(publication: Any, relative_name: str) -> None:
        if relative_name != SMOKE_TAG_PAGE:
            return
        (publication.staging / "escape-dir").symlink_to(
            outside, target_is_directory=True
        )

    probe_publication(
        monkeypatch, fail_on=SMOKE_TAG_PAGE, before_write=plant_link
    )

    with pytest.raises(IsADirectoryError):
        pretty_reports.write_pretty_reports(
            sample_result_set, base=tmp_artifact_root, environment=pretty_env
        )

    assert outside.is_dir()
    assert tree_digests(outside) == before
    assert not final.exists()
    assert publication_scratch(final) == frozenset()


def test_a_link_where_the_published_tree_belongs_is_refused(
    sample_result_set: Any,
    tmp_path: Path,
    tmp_artifact_root: Path,
    pretty_env: Environment,
) -> None:
    """A link standing in for the tree is refused before a page is rendered.

    The published tree is a directory this writer produced.  A link in its
    place is a redirection out of the artifact root, so it is refused, and it
    is refused **before** a staging tree is built.  The previous behaviour was
    to treat it as the tree to replace: the link was renamed aside like any
    previous generation and the new tree published over the name, which
    destroyed the entry the operator put there and left a link the cleanup
    could not remove sitting in the build output as scratch.

    The link, its destination and the build output are therefore all asserted
    afterwards: the destination is still empty, the entry is still the link,
    and no scratch was created.
    """
    outside = tmp_path / "link-destination"
    outside.mkdir()
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    final.parent.mkdir(parents=True)
    final.symlink_to(outside, target_is_directory=True)

    with pytest.raises(paths.ArtifactPathError):
        pretty_reports.write_pretty_reports(
            sample_result_set, base=tmp_artifact_root, environment=pretty_env
        )

    assert listed_names(outside) == frozenset()
    assert final.is_symlink()
    assert publication_scratch(final) == frozenset()


def test_a_symlinked_build_output_component_is_refused(
    sample_result_set: Any,
    tmp_path: Path,
    tmp_artifact_root: Path,
    pretty_env: Environment,
) -> None:
    """The review's probe, reproduced: nothing outside the root is removed.

    This is the case that made the finding blocking.  Every filesystem step of
    the publication used to be resolved from a pathname, and the *first* of
    them was the interrupted-publication recovery -- which listed the parent of
    the tree and removed any scratch carrying this process's id.  With a link
    standing in for ``target/``, that listing and that removal happened inside
    the link's destination, so a prepared directory **outside the artifact
    root** whose name looked like publication scratch was deleted, and deleted
    before the hardened directory creation further down ever got to refuse the
    write.

    Every component from the build output directory inward is now verified by
    opening it under the descriptor of its already verified parent, before
    anything is listed or removed, so the publication is refused at ``target``
    and the prepared directory -- and the file inside it -- are untouched.
    """
    outside = tmp_path / "prepared-elsewhere"
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    prepared = outside.joinpath(
        TREE_PARENT_DIR_NAME,
        scratch_name(final, paths.PUBLICATION_STAGING_INFIX, os.getpid()),
    )
    prepared.mkdir(parents=True)
    (prepared / "keep.txt").write_text(
        "not the writer's to delete\n", encoding="utf-8"
    )
    build_output = paths.target_root(tmp_artifact_root)
    build_output.symlink_to(outside, target_is_directory=True)

    with pytest.raises(paths.ArtifactPathError):
        pretty_reports.write_pretty_reports(
            sample_result_set, base=tmp_artifact_root, environment=pretty_env
        )

    assert prepared.is_dir()
    assert (prepared / "keep.txt").read_text(encoding="utf-8") == (
        "not the writer's to delete\n"
    )
    assert build_output.is_symlink()
    assert not final.exists()


def test_a_renamed_aside_tree_that_cannot_be_restored_is_left_in_place(
    sample_result_set: Any,
    tmp_artifact_root: Path,
    pretty_env: Environment,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A restore that fails leaves the copy where the log record says it is.

    The recovery rule is "restored, never removed", and the case that tests it
    is the one where the rename back cannot be made: the copy is then still
    the only complete generation in existence, and this call's own scratch
    names are otherwise cleared on sight -- so a renamed-aside copy carrying
    this process's id would have been removed moments after the log record
    said it was intact and could be renamed back by hand.

    It is skipped instead.  Driven by making the restore fail, with an
    asset-copy fault behind it so the call does not go on to publish a tree of
    its own and the copy is what a reader is left with.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    aside = plant_scratch(
        final,
        scratch_name(final, paths.PUBLICATION_SUPERSEDED_INFIX, os.getpid()),
    )

    def refuse_restore(self: Any, name: str) -> None:
        raise OSError(errno.EPERM, os.strerror(errno.EPERM), name)

    monkeypatch.setattr(
        paths.ArtifactDirectoryPublication, "restore_superseded", refuse_restore
    )
    refuse_the_asset_copy(monkeypatch)

    with caplog.at_level("WARNING", logger=pretty_reports.logger.name):
        with pytest.raises(FileNotFoundError):
            pretty_reports.write_pretty_reports(
                sample_result_set,
                base=tmp_artifact_root,
                environment=pretty_env,
            )

    assert aside.is_dir()
    assert (aside / "marker.txt").read_text(encoding="utf-8") == aside.name
    assert not final.exists()
    assert any(
        "can be renamed back by hand" in record.getMessage()
        for record in caplog.records
    ), [record.getMessage() for record in caplog.records]

# Hook results in the report tree
#
# The port registers the scenario-lifecycle teardown as a real hook, so a hook
# can fail on its own account, and the status this tree presents for an element
# folds every hook outcome into its verdict.  A tree that rendered only the
# steps therefore showed a red element above a column of green steps with
# nothing anywhere to explain it.
#
# The hooks of an element are reported in the generator's own two section
# containers, .hooks-before and .hooks-after, on every page that shows that
# element -- its feature page and each of its tag pages.  What is asserted
# below is the same three properties the sibling artifact is held to: every
# hook that did not pass is reported, a hook that passed is reported nowhere,
# and a hook is not a step -- not counted as one, not timed as one, and not a
# filter unit of its own.
# --------------------------------------------------------------------------

#: The two hook section containers, as the vendored stylesheet names them.
HOOKS_BEFORE_CLASS: Final[str] = "hooks-before"
HOOKS_AFTER_CLASS: Final[str] = "hooks-after"

#: The group labels the element tree writes into a hook brief's keyword span.
BEFORE_HOOK_LABEL: Final[str] = "Before hook"
AFTER_HOOK_LABEL: Final[str] = "After hook"

#: Where the port's own scenario-lifecycle hook lives, dotted as the model
#: records it.
AFTER_HOOK_LOCATION: Final[str] = "features.environment.after_scenario"

#: behave's hook-failure status, which is outside Cucumber's vocabulary and so
#: normalises to ``unknown``.
BEHAVE_HOOK_ERROR_STATUS: Final[str] = "hook_error"

#: A hook duration distinct from the step durations these cases use, so an
#: assertion that it is absent cannot be satisfied by a coincidence.
HOOK_DURATION_NS: Final[int] = 412_000_000


def build_hook(
    status: str,
    location: str | None = AFTER_HOOK_LOCATION,
    duration: int | None = HOOK_DURATION_NS,
    error_message: str | None = None,
    embeddings: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Build one hook entry, in the shape ``app/reporting/events.py`` records.

    :param status: The hook's own outcome, in behave's vocabulary.
    :param location: Dotted path of the hook implementation; ``None`` yields an
        empty ``match``, which is how the model records a hook without one.
    :param duration: Nanoseconds, or ``None`` to omit the key.
    :param error_message: The hook's failure text, omitted when ``None``.
    :param embeddings: Attachments hanging off the hook.
    :returns: The hook entry.
    """
    result: dict[str, Any] = {"status": status}
    if duration is not None:
        result["duration"] = duration
    if error_message is not None:
        result["error_message"] = error_message
    entry: dict[str, Any] = {
        "match": {"location": location} if location else {},
        "result": result,
    }
    if embeddings:
        entry["embeddings"] = [dict(embedding) for embedding in embeddings]
    return entry


def document_with_hooks(
    step_status: str = "passed",
    before: tuple[dict[str, Any], ...] = (),
    after: tuple[dict[str, Any], ...] = (),
    tags: tuple[dict[str, Any], ...] = ({"name": "@Hooked"},),
) -> dict[str, Any]:
    """A one-scenario, one-tag document whose element carries those hooks.

    One tag, so the same element is presented on a feature page and on a tag
    page and a hook report can be required on both.

    :param step_status: The status of the element's single step.
    :param before: Before-hook entries.
    :param after: After-hook entries.
    :param tags: Element-level tags.
    :returns: The merged result document.
    """
    element = build_element(
        "A scenario whose hooks are the story",
        (build_step("acts", step_status, duration=3_000_000),),
        tags=tags,
    )
    if before:
        element["before"] = list(before)
    if after:
        element["after"] = list(after)
    return {
        "features": [
            build_feature("Hooked.feature", "Hooked", (element,), tags=("@Hooked",))
        ]
    }


def hook_sections(page: ParsedPage, group_class: str) -> tuple[dict[str, str], ...]:
    """Every hook section of one group on ``page``, in document order.

    :param page: A parsed page.
    :param group_class: :data:`HOOKS_BEFORE_CLASS` or
        :data:`HOOKS_AFTER_CLASS`.
    :returns: The attributes of each matching container.
    """
    return tuple(
        attributes
        for _tag, attributes in page.elements
        if group_class in attributes.get("class", "").split()
    )


def test_a_hook_that_did_not_pass_is_reported_wherever_its_scenario_is(
    pretty_env: Environment,
) -> None:
    """The failing hook's group, implementation, badge and text, on both pages.

    Driven with a scenario whose only step passed, which is the case the gap
    was about: the element is presented failed on the strength of the hook
    alone, so the hook's own brief is the only thing on either page that can
    account for it.  The failure text is hostile, so one assertion covers the
    text reaching the reader and the markup in it not reaching the browser.
    """
    document = document_with_hooks(
        after=(build_hook("failed", error_message=HOSTILE_ERROR_MESSAGE),)
    )
    pages = parse_pages(pretty_reports.render_pretty_pages(document, environment=pretty_env))
    detail_pages = [
        page
        for name, page in pages.items()
        if name.startswith((FEATURE_PAGE_PREFIX, TAG_PAGE_PREFIX))
    ]
    assert len(detail_pages) == 2, sorted(pages)

    for page in detail_pages:
        sections = hook_sections(page, HOOKS_AFTER_CLASS)
        assert len(sections) == 1, page.name
        assert sections[0].get("data-report-status") == "failed", page.name

        text = page.normalized_text
        assert AFTER_HOOK_LABEL in text, page.name
        assert AFTER_HOOK_LOCATION in text, page.name
        assert HOSTILE_ERROR_MESSAGE in text, page.name

        # The dangerous spelling, not the bare tag name: every page carries
        # legitimate inline script of its own, so what proves the hook's text
        # was escaped is the absence of the fragment only the model supplies.
        assert 'alert("e")' not in page.html, page.name
        assert "&lt;script&gt;" in page.html, page.name
        assert "49" not in text.replace(HOSTILE_ERROR_MESSAGE, ""), page.name
        assert ("failed", "Failed") in {
            (badge.status, badge.label) for badge in page.badges
        }, page.name


def test_a_hook_status_outside_the_vocabulary_keeps_the_word_the_model_recorded(
    pretty_env: Environment,
) -> None:
    """A behave-only hook status is graded once, and an unreadable one keeps its word.

    Two halves, because the shared status model decides the first of them.
    ``hook_error`` is behave's name for an exception in hook code and
    ``app/reporting/aggregation.py``'s ``STATUS_ALIASES`` folds it onto
    ``failed``, which is the same answer ``target/cucumber.json`` publishes for
    it -- the whole point of that table being that one run is not graded
    differently depending on which artifact a reader opens.  The fold happens
    at ingress, in the decorated copy this folder renders, so the page badges
    such a hook **Failed** and the recorded word is simply not on it.

    The second half is the clause that word reaches when nothing in the
    project can read the status: the group is still reported rather than
    dropped, the badge reads Unknown because a badge's label is its status's
    accessible name on every surface of this port, and the recorded word
    travels as TEXT after the implementation, since a brief reading only
    "Unknown" would withhold the one word naming what happened.  It is
    asserted against the macro directly, with an undecorated element, because
    that is the only shape the clause is reachable from once the authority
    canonicalises every status it can name.
    """
    document = document_with_hooks(
        before=(build_hook(BEHAVE_HOOK_ERROR_STATUS, location=None),),
        after=(
            build_hook(
                BEHAVE_HOOK_ERROR_STATUS,
                error_message="RuntimeError: teardown exploded",
            ),
        ),
    )
    pages = pretty_reports.render_pretty_pages(document, environment=pretty_env)
    page = parse_page(
        *next(
            (name, html)
            for name, html in pages.items()
            if name.startswith(FEATURE_PAGE_PREFIX)
        )
    )

    assert len(hook_sections(page, HOOKS_BEFORE_CLASS)) == 1
    assert len(hook_sections(page, HOOKS_AFTER_CLASS)) == 1
    for group_class in (HOOKS_BEFORE_CLASS, HOOKS_AFTER_CLASS):
        assert (
            hook_sections(page, group_class)[0].get("data-report-status")
            == "failed"
        ), group_class

    text = page.normalized_text
    assert BEFORE_HOOK_LABEL in text
    assert AFTER_HOOK_LOCATION in text
    assert "RuntimeError: teardown exploded" in text
    assert STATUS_LABELS["failed"] in page.badge_labels()
    assert aggregation.status_token(BEHAVE_HOOK_ERROR_STATUS) == "failed"
    assert aggregation.STATUS_ALIASES[BEHAVE_HOOK_ERROR_STATUS] == "failed"

    # The unreadable status, through the macro and with no decoration in
    # front of it: reported, badged Unknown, and the word kept.  The
    # location-less hook is what shows the word standing in for the
    # implementation it has not got.
    tree = pretty_env.get_template("pretty/_element_tree.html").module
    fragment = parse_page(
        "hooks",
        str(
            tree.hooks_group(
                {
                    "type": SCENARIO_TYPE,
                    "keyword": "Scenario",
                    "name": "an unreadable hook status",
                    "steps": [build_step("acts", "passed", duration=1)],
                    "before": [build_hook(UNRECOGNISED_STATUS, location=None)],
                    "after": [
                        build_hook(
                            UNRECOGNISED_STATUS,
                            error_message="RuntimeError: teardown exploded",
                        )
                    ],
                }
            )
        ),
    )

    assert len(hook_sections(fragment, HOOKS_BEFORE_CLASS)) == 1
    assert len(hook_sections(fragment, HOOKS_AFTER_CLASS)) == 1
    for group_class in (HOOKS_BEFORE_CLASS, HOOKS_AFTER_CLASS):
        assert (
            hook_sections(fragment, group_class)[0].get("data-report-status")
            == pretty_reports.UNKNOWN_STATUS
        ), group_class

    fragment_text = fragment.normalized_text
    assert f"{BEFORE_HOOK_LABEL} {UNRECOGNISED_STATUS}" in fragment_text
    assert f"{AFTER_HOOK_LOCATION} ({UNRECOGNISED_STATUS})" in fragment_text
    assert STATUS_LABELS[pretty_reports.UNKNOWN_STATUS] in fragment.badge_labels()
    assert pretty_reports.status_token(UNRECOGNISED_STATUS) == (
        pretty_reports.UNKNOWN_STATUS
    )


def test_a_hook_that_passed_adds_no_section_and_its_screenshot_still_renders(
    sample_result_set: Any, pretty_env: Environment
) -> None:
    """The ordinary shape adds not one node, and the embedding is unaffected.

    Every scenario of a healthy run carries a passed after-hook, and the sample
    document's failing scenario carries one with a screenshot on it, so a
    section emitted for a hook that passed would appear on nearly every element
    of every page.  The sample document is used for exactly that reason: it is
    the shape a real run produces, and its one embedding must still be inlined.
    """
    pages = parse_pages(
        pretty_reports.render_pretty_pages(sample_result_set, environment=pretty_env)
    )
    for name, page in pages.items():
        assert hook_sections(page, HOOKS_BEFORE_CLASS) == (), name
        assert hook_sections(page, HOOKS_AFTER_CLASS) == (), name
        assert AFTER_HOOK_LABEL not in page.text, name

    assert any(
        PNG_DATA_URI_PREFIX in page.html for page in pages.values()
    ), "the sample document's screenshot is no longer inlined"


def test_hook_reports_are_not_steps_and_carry_no_duration(
    pretty_env: Environment,
) -> None:
    """The section is outside the step vocabulary, and prints no duration.

    Two properties of one decision: a hook is not a step.  So the section is
    neither a ``.steps`` group nor a ``.step`` -- nothing that counts or walks
    steps can pick a hook up, and the steps overview's own exclusion of hook
    entries stays true of this tree as well -- and it prints no duration, since
    an element's duration here is the sum of its step durations alone, exactly
    as the authority's ``Element.getDuration()`` sums them.  The duration
    assertion is made against the same document rendered without the hook, so
    it is the hook's own duration that is absent.
    """
    after = build_hook("failed", error_message="teardown failed")
    hooked = pretty_reports.render_pretty_pages(
        document_with_hooks(after=(after,)), environment=pretty_env
    )
    plain = pretty_reports.render_pretty_pages(
        document_with_hooks(), environment=pretty_env
    )
    name = next(page for page in hooked if page.startswith(FEATURE_PAGE_PREFIX))
    page = parse_page(name, hooked[name])

    sections = hook_sections(page, HOOKS_AFTER_CLASS)
    assert len(sections) == 1
    classes = sections[0]["class"].split()
    assert "steps" not in classes
    assert "step" not in classes
    assert "data-report-filterable" not in sections[0]

    def step_containers(markup: str) -> int:
        """Count the step containers of one page."""
        return sum(
            1
            for _tag, attributes in parse_page("counted", markup).elements
            if "step" in attributes.get("class", "").split()
        )

    plain_name = next(page for page in plain if page.startswith(FEATURE_PAGE_PREFIX))
    assert step_containers(hooked[name]) == step_containers(plain[plain_name])

    text = page.normalized_text
    for rendering in (
        str(HOOK_DURATION_NS),
        f"{HOOK_DURATION_NS / 1_000_000_000:.3f}",
        f"{HOOK_DURATION_NS / 1_000_000_000:.2f}",
    ):
        assert rendering not in text, rendering
