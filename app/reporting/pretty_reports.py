"""HTML contract 2: the generated PrettyReports tree of report pages.

This module is the port of the PrettyReports plugin the Java build ran, and it
is a **separate contract** from ``app/reporting/html_report.py``.  The Java
build produced this tree with ``me.jvt.cucumber:reporting-plugin`` 7.2.0
(``pom.xml:66-70``), which pulls ``net.masterthought:cucumber-reporting`` 5.6.1
over ``org.apache.velocity:velocity-engine-core`` 2.3 -- a different generator
from ``io.cucumber:html-formatter`` 17.0.0, with a different page set, a
different layout, different assets and different navigation.  The only markup
the two contracts share is the three partials in ``app/templates/partials/``.

What this module owns
---------------------
1. **Every emitted filename and every link.**  The detail pages are named by a
   numeric hash the generator computes, so nothing may invent a name:
   :func:`to_valid_file_name`, :func:`feature_page_name` and
   :func:`tag_page_name` reproduce ``net.masterthought.cucumber.util.Util``'s
   ``toValidFileName`` and its two call sites exactly (see
   :func:`java_hash_code` for the arithmetic and the four committed filenames
   it was verified against).  Every template in ``app/templates/pretty/``
   receives its own output filename and every ``href`` as a passed-in value
   and constructs none of them.
2. **Which features and which tags appear at all.**  A scenario the tag
   expression did not select never ran, so it never reached the reference
   generator; a feature with nothing selected is therefore omitted altogether
   (:func:`emitted_features`), and a tag carried only by unselected scenarios
   gets no page (:func:`collect_tags`).
3. **The tags-overview rows.**  ``pretty/overview_tags.html`` is the one page
   with no primary derivation of its own, so :func:`build_tag_rows` and
   :func:`build_tag_totals` aggregate them here, mirroring
   ``pretty/tag.html``'s tally rule for rule so the overview row and the tag
   page's own row cannot disagree.
4. **Rendering and writing.**  :func:`render_pretty_pages` maps output
   filename to HTML and touches no file; :func:`copy_pretty_assets` performs
   the byte copy of the vendored asset set; :func:`write_pretty_reports`
   composes the two.

What this module deliberately does *not* own
--------------------------------------------
* **Markup.**  All six page templates and their four helpers already exist
  under ``app/templates/pretty/``.  The three overview pages that can derive
  their own rows do so -- their arithmetic was taken from the generator's
  bytecode and is documented in the templates themselves -- so this module
  passes the normalized result model straight through rather than aggregating
  a second time and risking two answers to one question.
* **The tablesorter initialisation.**  ``pretty/_layout.html`` emits it
  exactly once per page.  A second copy must never be added here.
* **Deleting anything.**  :mod:`app.utils.paths` creates directories and never
  removes them, and emptying the build-output directory is ``app/cli.py``'s
  ``--clean`` step.  This module overwrites its files in place.
* **Status or duration presentation.**  ``pretty/_macros.html`` owns duration,
  count, percentage and timestamp formatting and
  ``partials/status_badge.html`` owns status normalisation, so raw nanosecond
  integers and raw status strings are passed through untouched.

Boundaries
----------
* Imports are limited to the standard library, :mod:`jinja2`,
  :mod:`app.reporting.events` (the result schema) and :mod:`app.utils.paths`
  (every path).  Nothing in ``app/reporting`` imports a service: the plan's
  dependency edge runs ``services -> reporting`` and never the reverse.
* Flask is **not** imported and no application, request or test context is
  ever needed: this writer runs inside a worker process that never builds an
  application, so it builds its own :class:`jinja2.Environment` in
  :func:`build_environment` instead of reaching for the framework's own
  template helper.
* No path literal appears here.  Directories come from
  :func:`app.utils.paths.pretty_reports_html_dir`,
  :func:`~app.utils.paths.vendor_dir`, :func:`~app.utils.paths.static_dir` and
  :func:`~app.utils.paths.templates_dir`.  The emitted *page* and *asset*
  names below are the artifact contract itself -- the same four navigation
  targets ``pretty/_layout.html`` hard-codes -- and are declared once each.

Determinism
-----------
Byte stability across runs is impossible (durations, timestamps and screenshot
bytes vary by construction), so what is guaranteed is **structure**: features
in the model's own source order, scenarios in line order, Background
occurrences left where they are, tag pages in first-appearance order, and the
same page set for the same input.  Nothing here sorts: the publisher's
``sortingMethod: 'ALPHABETICAL'`` (``Jenkins:15``) is a display option of its
own and imposes nothing on the artifacts.  The one value that legitimately
differs between two renders of the same document is the build date, and only
when the document carries neither ``generated_at`` nor ``started_at``.

Failure behaviour
-----------------
A **test outcome never raises**: failures, undefined steps, skipped steps, an
empty selection and an absent tag set are all data that render.  Malformed
model input is tolerated defensively rather than raised on, because a report is
what a reader turns to when a run has gone wrong.  Only genuine render or I/O
faults propagate (:class:`jinja2.TemplateError`, :class:`OSError`), becoming
the command line's writer-failure exit class -- under which the pages written
before the fault deliberately remain on disk, which matters here because this
writer emits many files rather than one.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, FileSystemLoader

from app.reporting.events import ELEMENT_TYPE_SCENARIO, JsonDict, ResultSet
from app.utils.paths import (
    PRETTY_OVERVIEW_INDEX,
    ensure_dir,
    pretty_reports_html_dir,
    static_dir,
    templates_dir,
    vendor_dir,
)

__all__ = [
    "ASSET_SUBDIRECTORIES",
    "DEFAULT_PROJECT_NAME",
    "FEATURE_PAGE_PREFIX",
    "FEATURE_TEMPLATE",
    "INT32_MAX",
    "KNOWN_STATUSES",
    "MONTH_ABBREVIATIONS",
    "OVERVIEW_FAILURES_PAGE",
    "OVERVIEW_FEATURES_PAGE",
    "OVERVIEW_STEPS_PAGE",
    "OVERVIEW_TAGS_PAGE",
    "OVERVIEW_TEMPLATES",
    "PAGE_LINKED_ASSETS",
    "PAGE_SUFFIX",
    "PORT_ASSETS",
    "STATUS_PRECEDENCE",
    "TAG_PAGE_PREFIX",
    "TAG_TEMPLATE",
    "UNKNOWN_STATUS",
    "VENDORED_FONT_ASSETS",
    "build_environment",
    "build_tag_rows",
    "build_tag_totals",
    "collect_tags",
    "copy_pretty_assets",
    "element_duration_ns",
    "element_status",
    "emitted_features",
    "feature_href_map",
    "feature_page_name",
    "format_build_date",
    "java_hash_code",
    "render_pretty_pages",
    "status_token",
    "tag_href_map",
    "tag_page_name",
    "to_valid_file_name",
    "worst_status",
    "write_pretty_reports",
]

#: Module logger.  Deliberately without a handler of its own, matching the
#: sibling writers: the command-line entry point installs the handler split
#: that routes WARNING-and-above to stderr, and Python's ``lastResort`` handler
#: covers a bare import in a test.
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# The filename hash
# --------------------------------------------------------------------------- #

#: ``java.lang.Integer.MAX_VALUE``.  ``Util.toValidFileName`` adds it to the
#: hash "to eliminate minus character which might be returned by hashCode()".
INT32_MAX: Final[int] = 2147483647

#: Mask, sign boundary and modulus of Java's 32-bit ``int``, used to truncate
#: the hash exactly as the JVM does before it is widened to a ``long``.
_UINT32_MASK: Final[int] = 0xFFFFFFFF
_INT32_SIGN_BOUND: Final[int] = 0x80000000
_UINT32_MODULUS: Final[int] = 0x100000000

#: The multiplier of ``java.lang.String.hashCode``: ``h = 31 * h + unit``.
_HASH_MULTIPLIER: Final[int] = 31

#: Boundary of the Basic Multilingual Plane and the surrogate-pair constants.
#: A Python ``str`` holds code points while a Java ``String`` holds UTF-16 code
#: units, so a non-BMP character contributes *two* units to the hash.
_BMP_MAX: Final[int] = 0xFFFF
_SURROGATE_OFFSET: Final[int] = 0x10000
_HIGH_SURROGATE_BASE: Final[int] = 0xD800
_LOW_SURROGATE_BASE: Final[int] = 0xDC00
_LOW_SURROGATE_MASK: Final[int] = 0x3FF
_HIGH_SURROGATE_SHIFT: Final[int] = 10

# --------------------------------------------------------------------------- #
# Emitted page names.  These are the artifact contract, not filesystem paths:
# they are the four navigation targets ``pretty/_layout.html`` hard-codes and
# the two detail-page shapes the reference tree carries, so they are declared
# once here and reach the templates as passed-in values.
# --------------------------------------------------------------------------- #

#: Suffix of every emitted page.
PAGE_SUFFIX: Final[str] = ".html"

#: The overview index, and the page served when a request names the artifact
#: directory itself.  Taken from :mod:`app.utils.paths` so the name this module
#: writes and the name that module resolves have a single owner.
OVERVIEW_FEATURES_PAGE: Final[str] = PRETTY_OVERVIEW_INDEX

#: The other three overview pages.
OVERVIEW_TAGS_PAGE: Final[str] = f"overview-tags{PAGE_SUFFIX}"
OVERVIEW_STEPS_PAGE: Final[str] = f"overview-steps{PAGE_SUFFIX}"
OVERVIEW_FAILURES_PAGE: Final[str] = f"overview-failures{PAGE_SUFFIX}"

#: Detail-page prefixes.  ``Feature.calculateReportFileName`` and
#: ``Tag.generateFileName`` are the two call sites of ``toValidFileName``.
FEATURE_PAGE_PREFIX: Final[str] = "report-feature_"
TAG_PAGE_PREFIX: Final[str] = "report-tag_"

#: Output filename -> template name for the four overview pages, in the order
#: they are rendered and written.  **Template names use underscores and output
#: filenames use hyphens**; the two never follow one another.
OVERVIEW_TEMPLATES: Final[tuple[tuple[str, str], ...]] = (
    (OVERVIEW_FEATURES_PAGE, "pretty/overview_features.html"),
    (OVERVIEW_TAGS_PAGE, "pretty/overview_tags.html"),
    (OVERVIEW_STEPS_PAGE, "pretty/overview_steps.html"),
    (OVERVIEW_FAILURES_PAGE, "pretty/overview_failures.html"),
)

#: The two detail-page templates.
FEATURE_TEMPLATE: Final[str] = "pretty/feature.html"
TAG_TEMPLATE: Final[str] = "pretty/tag.html"

# --------------------------------------------------------------------------- #
# The asset set.  Reproduced byte for byte from ``app/static/vendor`` -- the
# bundled Bootstrap, jQuery, Chart.js, tablesorter, Moment and icon fonts are
# the reference generator's own output and a report contract, NOT a design
# system this port adopts, so nothing here upgrades, swaps, links to a CDN,
# tree-shakes or restyles them.
# --------------------------------------------------------------------------- #

#: The four asset sub-directories, which map 1:1 from ``app/static/vendor``
#: onto the emitted tree.  Declared for documentation and for the copy's
#: deterministic ordering; the copy itself walks the vendor directory, so a
#: file added there travels without a code change.
ASSET_SUBDIRECTORIES: Final[tuple[str, ...]] = ("css", "js", "fonts", "images")

#: The port's own two assets, as ``(source relative to app/static, destination
#: relative to the emitted tree)``.  ``pretty/_layout.html`` links both
#: tree-relative in addition to the vendored files, so omitting either ships
#: every page with two broken references.
PORT_ASSETS: Final[tuple[tuple[str, str], ...]] = (
    ("css/main.css", "css/main.css"),
    ("js/report.js", "js/report.js"),
)

#: Every asset an emitted page links by name, in ``pretty/_layout.html``'s own
#: order: the five scripts, the four stylesheets and the shortcut icon.  Their
#: presence is verified after the copy, because a page that links a missing
#: file is a broken artifact rather than a cosmetic problem.  Note
#: ``Chart.min.js``'s capital C -- a lower-case copy is a 404 on any
#: case-sensitive filesystem, which is every Linux build agent.
PAGE_LINKED_ASSETS: Final[tuple[str, ...]] = (
    "js/jquery.min.js",
    "js/jquery.tablesorter.min.js",
    "js/Chart.min.js",
    "js/moment.min.js",
    "js/bootstrap.min.js",
    "css/bootstrap.min.css",
    "css/cucumber.css",
    "css/font-awesome.min.css",
    "css/main.css",
    "js/report.js",
    "images/favicon.png",
)

#: The eleven font files the two vendored stylesheets request as
#: ``url(../fonts/<name>)``.  They are referenced from CSS rather than from a
#: page, so an absent one is reported as a warning instead of failing the
#: write: the pages still render, with the icon fonts falling back.
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

# --------------------------------------------------------------------------- #
# Status vocabulary.  Mirrors ``partials/status_badge.html``'s ``status_token``
# and ``pretty/_element_tree.html``'s ``STATUS_PRECEDENCE`` exactly, because
# the tags-overview rows this module builds must agree cell for cell with the
# tag page's own tally, which those macros compute.
# --------------------------------------------------------------------------- #

#: Every status token the templates recognise.
KNOWN_STATUSES: Final[tuple[str, ...]] = (
    "passed",
    "failed",
    "skipped",
    "pending",
    "undefined",
    "untested",
    "ambiguous",
)

#: What an unrecognised, blank or absent status normalises to.  A status the
#: model never produced must not be reported as a pass.
UNKNOWN_STATUS: Final[str] = "unknown"

#: Severity order, highest first: the first member that occurs among a set of
#: step statuses is the status of the scenario or feature they belong to.
STATUS_PRECEDENCE: Final[tuple[str, ...]] = (
    "failed",
    "undefined",
    "ambiguous",
    "pending",
    "skipped",
    "untested",
    "passed",
)

#: The nine count keys ``pretty/_stats_table.html`` reads on a row and sums in
#: its footer.
_COUNT_KEYS: Final[tuple[str, ...]] = (
    "steps_passed",
    "steps_failed",
    "steps_skipped",
    "steps_pending",
    "steps_undefined",
    "steps_total",
    "scenarios_passed",
    "scenarios_failed",
    "scenarios_total",
)

# --------------------------------------------------------------------------- #
# Page identity
# --------------------------------------------------------------------------- #

#: The Project cell of every page's build-info table.  The reference carries
#: the Java generator's own placeholder ("No Name (add projectName to
#: cucumber-reporting.properties)"); this port states its own name instead,
#: which is ``pyproject.toml``'s ``[project] name`` and the name the layout's
#: footer already credits.
DEFAULT_PROJECT_NAME: Final[str] = "testinium-qa"

#: English month abbreviations, used instead of ``strftime('%b')`` on purpose.
#: The reference build date reads "07 Sep 2022, 15:39", and ``%b`` follows
#: whatever locale a process happens to carry -- this suite deliberately runs
#: one scenario under a French-locale browser environment, so a locale-
#: dependent month name would make the artifact non-deterministic.
MONTH_ABBREVIATIONS: Final[tuple[str, ...]] = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


# --------------------------------------------------------------------------- #
# The filename hash: ``net.masterthought.cucumber.util.Util.toValidFileName``
# --------------------------------------------------------------------------- #


def _utf16_units(text: str) -> Iterable[int]:
    """Yield the UTF-16 code units of ``text``, as a Java ``String`` holds them.

    A Python :class:`str` is a sequence of code points; a Java ``String`` is a
    sequence of UTF-16 code units, and ``String.hashCode`` iterates the latter.
    The two agree for every character in the Basic Multilingual Plane and
    differ for everything above it, where one code point is a surrogate
    *pair* and therefore contributes two terms to the hash.  Feature URIs and
    tag names in this suite are ASCII, so the distinction never shows up in the
    verified values -- it is reproduced anyway, because a hash that silently
    disagrees with the generator on one input is a filename nobody can predict.

    Args:
        text: The string to decompose.

    Yields:
        Each UTF-16 code unit as an ``int`` in ``[0, 0xFFFF]``.
    """
    for character in text:
        code_point = ord(character)
        if code_point > _BMP_MAX:
            offset = code_point - _SURROGATE_OFFSET
            yield _HIGH_SURROGATE_BASE + (offset >> _HIGH_SURROGATE_SHIFT)
            yield _LOW_SURROGATE_BASE + (offset & _LOW_SURROGATE_MASK)
        else:
            yield code_point


def java_hash_code(text: str) -> int:
    """Return ``java.lang.String.hashCode()`` for ``text`` as a signed 32-bit int.

    The JVM's definition is ``s[0]*31^(n-1) + s[1]*31^(n-2) + ... + s[n-1]``
    evaluated in 32-bit two's-complement arithmetic, which overflows silently.
    That overflow is *load-bearing*: it is why the reference filenames are the
    numbers they are, so the accumulator is masked to 32 bits on every
    iteration and then reinterpreted as signed.

    Args:
        text: The string to hash.  An empty string hashes to ``0``, as in Java.

    Returns:
        The hash in ``[-2147483648, 2147483647]``.

    Raises:
        TypeError: If ``text`` is not a :class:`str`.  This is a programming
            error at a call site, not report data, so it is raised rather than
            coerced -- a coerced value would produce a filename that no test
            could have predicted.

    Examples:
        >>> java_hash_code("")
        0
        >>> java_hash_code("@Smoke")
        1912275215
        >>> java_hash_code("polygenelubricants")  # the Integer.MIN_VALUE case
        -2147483648
    """
    if not isinstance(text, str):
        raise TypeError(f"expected str, got {type(text).__name__}")
    accumulator = 0
    for unit in _utf16_units(text):
        accumulator = (accumulator * _HASH_MULTIPLIER + unit) & _UINT32_MASK
    if accumulator >= _INT32_SIGN_BOUND:
        accumulator -= _UINT32_MODULUS
    return accumulator


def to_valid_file_name(text: str) -> str:
    """Reproduce ``Util.toValidFileName``: the Java hash plus ``Integer.MAX_VALUE``.

    The generator's own implementation, comment included, is::

        public static String toValidFileName(String fileName) {
            // adds MAX_VALUE to eliminate minus character which might be
            // returned by hashCode()
            return Long.toString((long) fileName.hashCode() + Integer.MAX_VALUE);
        }

    The cast to ``long`` before the addition is the detail that matters: the
    sum is computed in 64 bits, so it is **not** an unsigned 32-bit value and
    must not be masked to one.  The result therefore spans
    ``[-1, 4294967294]``: a hash of ``0`` yields ``2147483647`` and a hash of
    ``Integer.MIN_VALUE`` yields ``-1``, which a ``& 0xFFFFFFFF`` would turn
    into ``4294967295`` and so name a file the Java generator never wrote.

    Args:
        text: The value to hash -- a full ``file:``-prefixed feature URI for a
            feature page, or a tag name including its leading ``@`` for a tag
            page.

    Returns:
        The hash as a decimal string, ready to be spliced into a filename.

    Raises:
        TypeError: If ``text`` is not a :class:`str`; see :func:`java_hash_code`.

    Examples:
        Verified against all four filenames committed under the reference
        build's report tree:

        >>> to_valid_file_name("@Smoke")            # report-tag_4059758862.html
        '4059758862'
        >>> to_valid_file_name("@Dash")             # report-tag_2208711665.html
        '2208711665'
        >>> to_valid_file_name("file:src/main/resources/features/Crm.feature")
        '1735223818'
        >>> to_valid_file_name("file:src/main/resources/features/Contact.feature")
        '3146354636'
        >>> to_valid_file_name("")
        '2147483647'
        >>> to_valid_file_name("polygenelubricants")
        '-1'
    """
    return str(java_hash_code(text) + INT32_MAX)


def feature_page_name(uri: str) -> str:
    """Return the detail-page filename for a feature URI.

    Reproduces ``Feature.calculateReportFileName(jsonFileNo)``::

        "report-feature_" + (jsonFileNo > 0 ? jsonFileNo + "_" : "")
                          + toValidFileName(uri) + ".html"

    **The numeration segment is never emitted.**  ``jsonFileNo`` exists only to
    disambiguate the same feature appearing in two different input JSON files,
    and this port merges every worker's results into one document before any
    page is rendered, so it is always ``0``.

    The hashed input is the **full ``file:``-prefixed URI**, not the bare path
    and not the feature name.  Two consequences follow, and both are intended:
    because the features moved to ``features/``, the feature-page filenames
    necessarily differ from the committed ones; and because the hash is over
    the URI rather than the feature id, the two pairs of features in this
    suite that share an id -- Contact with Inventory and Login with Notes,
    each pair sharing a title -- still get two distinct pages.

    Args:
        uri: The feature's ``uri`` from the result model, e.g.
            ``"file:features/Crm.feature"``.

    Returns:
        The page filename, e.g. ``"report-feature_1364259633.html"``.

    Raises:
        TypeError: If ``uri`` is not a :class:`str`; see :func:`java_hash_code`.

    Examples:
        >>> feature_page_name("file:features/Crm.feature")
        'report-feature_1364259633.html'
        >>> feature_page_name("file:features/Contact.feature")
        'report-feature_2292591987.html'
        >>> feature_page_name("file:features/Inventory.feature")
        'report-feature_3306537903.html'
    """
    return f"{FEATURE_PAGE_PREFIX}{to_valid_file_name(uri)}{PAGE_SUFFIX}"


def tag_page_name(tag_name: str) -> str:
    """Return the detail-page filename for a tag.

    Reproduces ``Tag.generateFileName``::

        String.format("report-tag_%s.html", toValidFileName(tagName))

    The hashed input **includes the leading ``@``**, which is part of the tag
    name in this port's result model as it was in the JVM's.  Tag text does not
    move with the feature directory, so tag-page filenames are unchanged from
    the committed ones: ``@Smoke`` is ``report-tag_4059758862.html`` here and
    in the reference tree.

    Args:
        tag_name: The tag, e.g. ``"@Smoke"``.

    Returns:
        The page filename, e.g. ``"report-tag_4059758862.html"``.

    Raises:
        TypeError: If ``tag_name`` is not a :class:`str`; see
            :func:`java_hash_code`.

    Examples:
        >>> tag_page_name("@Smoke")
        'report-tag_4059758862.html'
        >>> tag_page_name("@UPGN-286")
        'report-tag_341483364.html'
    """
    return f"{TAG_PAGE_PREFIX}{to_valid_file_name(tag_name)}{PAGE_SUFFIX}"


# --------------------------------------------------------------------------- #
# Defensive model readers.
#
# The result document is plain JSON-serialisable data, and a report is exactly
# what a reader turns to when a run has gone wrong, so a malformed node is
# skipped rather than raised on.  The page templates apply the same discipline
# to the same values; these helpers keep the Python side in step.
# --------------------------------------------------------------------------- #


def _as_mapping(value: Any) -> JsonDict:
    """Return ``value`` when it is a mapping, otherwise an empty one."""
    return value if isinstance(value, dict) else {}


def _mappings(value: Any) -> list[JsonDict]:
    """Return the mapping members of ``value``, or an empty list.

    A string is a sequence of characters and a mapping a sequence of keys;
    neither is a list of model nodes, so both are rejected outright rather
    than iterated one character or one key at a time.
    """
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [item for item in value if isinstance(item, dict)]
    return []


def _as_text(value: Any) -> str:
    """Return ``value`` stripped when it is a string, otherwise ``""``."""
    return value.strip() if isinstance(value, str) else ""


def _is_selected(element: JsonDict) -> bool:
    """Answer whether the tag expression selected ``element``.

    Absent means selected: a document without the key predates the flag.  Only
    an explicit ``False`` excludes an element, matching ``pretty/tag.html``'s
    ``false if (selected is boolean and not selected) else true``.

    Args:
        element: A Background or scenario element.

    Returns:
        ``False`` only when the element carries ``"selected": False``.
    """
    return element.get("selected", True) is not False


def _is_scenario(element: JsonDict) -> bool:
    """Answer whether ``element`` is a scenario rather than a Background.

    A Background occurrence is not a test case however many times it appears,
    so it is never counted as a scenario -- the generator's own
    ``isScenario()`` is ``"scenario".equalsIgnoreCase(type)``, which this
    reproduces case-insensitively.

    Args:
        element: A Background or scenario element.

    Returns:
        ``True`` when the element's ``type`` is ``"scenario"``.
    """
    return _as_text(element.get("type")).lower() == ELEMENT_TYPE_SCENARIO


def status_token(status: Any) -> str:
    """Normalise a raw status exactly as ``partials/status_badge.html`` does.

    The single normalisation point on the Python side.  It exists so that the
    tags-overview rows built here and the tag page's own tally -- computed in
    the templates from the same rule -- cannot answer differently.

    Args:
        status: A raw ``result.status`` value, or anything at all.

    Returns:
        The status in lower case when it is one of :data:`KNOWN_STATUSES`,
        otherwise :data:`UNKNOWN_STATUS`.  A blank, absent or unrecognised
        status is deliberately not treated as a pass.

    Examples:
        >>> status_token("Passed")
        'passed'
        >>> status_token(None)
        'unknown'
        >>> status_token("executing")
        'unknown'
    """
    candidate = _as_text(status).lower()
    return candidate if candidate in KNOWN_STATUSES else UNKNOWN_STATUS


def worst_status(tokens: Iterable[str], empty: str = "passed") -> str:
    """Fold already-normalised status tokens by :data:`STATUS_PRECEDENCE`.

    Args:
        tokens: Normalised tokens, in any order.
        empty: What to answer for an empty collection, or for one holding
            nothing the precedence names.  The default is ``"passed"``, which
            is measured rather than chosen: ``EmployeeFc.feature`` declares a
            Background with an empty body, and the reference generator renders
            each of its step-less occurrences as passed.  A caller asking a
            run-level question can pass :data:`UNKNOWN_STATUS` instead.

    Returns:
        The most severe token present, or ``empty``.

    Examples:
        >>> worst_status(["passed", "skipped", "failed"])
        'failed'
        >>> worst_status([])
        'passed'
        >>> worst_status(["unknown"], empty="unknown")
        'unknown'
    """
    present = set(tokens)
    for candidate in STATUS_PRECEDENCE:
        if candidate in present:
            return candidate
    return empty


def _step_tokens(element: JsonDict) -> list[str]:
    """Return the normalised status of every step of ``element``, in order."""
    return [
        status_token(_as_mapping(step.get("result")).get("status"))
        for step in _mappings(element.get("steps"))
    ]


def element_status(element: JsonDict, empty: str = "passed") -> str:
    """Return the status of one element, decided by its own steps alone.

    A scenario is not coloured by its neighbours and a Background occurrence is
    not coloured by the scenario that follows it, which is what
    ``pretty/_element_tree.html``'s ``element_status`` macro implements.

    Args:
        element: A Background or scenario element.
        empty: Answer for an element with no steps; see :func:`worst_status`.

    Returns:
        The most severe status among the element's steps.
    """
    return worst_status(_step_tokens(element), empty=empty)


def element_duration_ns(element: JsonDict) -> int:
    """Return the element's duration as a nanosecond integer.

    The sum of its **step** durations and nothing else: hook durations are
    never added, so the after-hook that carries a failure screenshot does not
    lengthen its scenario.  A duration that is absent, a boolean, a float or
    negative counts as *no sample* rather than as zero, so one malformed step
    cannot poison the sum, and an element with no samples answers ``0`` --
    which the generator's own arithmetic also produces and which the macros
    render as ``0.000``.  A skipped step legitimately carries no ``duration``
    key at all, so this path is ordinary rather than exceptional.

    Args:
        element: A Background or scenario element.

    Returns:
        The total in nanoseconds, never negative.

    Examples:
        >>> element_duration_ns({"steps": [{"result": {"duration": 5}},
        ...                                {"result": {"status": "skipped"}}]})
        5
        >>> element_duration_ns({})
        0
    """
    total = 0
    for step in _mappings(element.get("steps")):
        duration = _as_mapping(step.get("result")).get("duration")
        # ``bool`` is a subclass of ``int``; True would otherwise add one
        # nanosecond and hide a malformed document.
        if (
            isinstance(duration, int)
            and not isinstance(duration, bool)
            and duration >= 0
        ):
            total += duration
    return total


# --------------------------------------------------------------------------- #
# What the tree contains: features, links and tags
# --------------------------------------------------------------------------- #


def emitted_features(result_set: ResultSet | None) -> list[JsonDict]:
    """Return the features that get a row and a detail page, in source order.

    A feature whose every element was excluded by the tag expression never ran,
    so the JVM never started it and it is absent from the JSON report
    -- the default ``@Smoke`` run holds exactly one feature where the suite has
    ten.  Emitting nine rows of zeros beside it would contradict the artifact
    this tree is generated alongside, so such a feature is omitted altogether,
    which is also what ``pretty/overview_features.html`` does with its own
    derivation.  A feature with no elements at all is omitted on the same
    ground.

    Nothing is sorted: the model's order is source order and it is preserved.

    Args:
        result_set: The merged result document, or ``None`` for a run that
            produced nothing -- the artifacts are still written in that case,
            so ``None`` yields an empty list rather than an error.

    Returns:
        The feature mappings to render, in document order.
    """
    document = _as_mapping(result_set)
    kept: list[JsonDict] = []
    for feature in _mappings(document.get("features")):
        elements = _mappings(feature.get("elements"))
        if any(_is_selected(element) for element in elements):
            kept.append(feature)
    return kept


def feature_href_map(features: Sequence[JsonDict]) -> dict[str, str]:
    """Map every key a template might hold to a feature's detail-page filename.

    Two templates look a feature's link up, and they consult different keys:
    ``pretty/overview_features.html`` tries ``uri`` then ``path``, while
    ``pretty/overview_failures.html`` tries ``uri``, ``path``, ``id`` then
    ``name``.  One mapping keyed on all four satisfies both, and neither
    template ever computes a filename.

    **A key that would point at two different pages is dropped rather than
    resolved.**  ``uri`` and ``path`` are unique per feature, but this suite's
    ids and names are not: Contact and Inventory share a title and therefore an
    id, as do Login and Notes.  Keeping a colliding key would silently link one
    feature's failure to the other feature's page; dropping it makes the
    template fall back to plain text, and costs nothing in practice because
    both consumers try ``uri`` first and the URI is always present.

    Args:
        features: The features being emitted, from :func:`emitted_features`.

    Returns:
        A mapping from ``uri``, ``path``, ``id`` and ``name`` to the page
        filename, with every ambiguous key removed.
    """
    candidates: dict[str, set[str]] = {}
    for feature in features:
        uri = _as_text(feature.get("uri"))
        if not uri:
            # No URI, no hash input, and therefore no page: the feature still
            # renders as a row, without a link.
            logger.warning(
                "Feature %r carries no uri, so it gets no detail page link",
                _as_text(feature.get("name")),
            )
            continue
        href = feature_page_name(uri)
        for key in (
            uri,
            _as_text(feature.get("path")),
            _as_text(feature.get("id")),
            _as_text(feature.get("name")),
        ):
            if key:
                candidates.setdefault(key, set()).add(href)
    return {
        key: next(iter(hrefs))
        for key, hrefs in candidates.items()
        if len(hrefs) == 1
    }


def _tag_names(tags: Any) -> list[str]:
    """Return the tag names of a model ``tags`` value, in declaration order.

    Both model shapes are accepted, as the templates accept them: a feature tag
    is a mapping carrying ``name``, ``type`` and ``location``, an element tag a
    mapping carrying ``name`` alone, and a bare string is accepted too so a
    hand-built fixture behaves.  The leading ``@`` is part of the name and is
    kept -- it is part of the hash input.
    """
    names: list[str] = []
    if not isinstance(tags, Sequence) or isinstance(tags, (str, bytes)):
        return names
    for item in tags:
        name = _as_text(item.get("name")) if isinstance(item, dict) else _as_text(item)
        if name:
            names.append(name)
    return names


def collect_tags(features: Sequence[JsonDict]) -> dict[str, list[JsonDict]]:
    """Group the selected scenarios of ``features`` by the tags they carry.

    The tag set of the run, and the order the tag pages are emitted in.

    Three rules, each of them measured:

    * **Feature-level tags propagate onto every scenario.**  ``@Smoke`` is
      declared once at the top of ``Crm.feature`` and every scenario element of
      that feature carries it, so a feature's own tags are attributed to each
      of its scenarios -- exactly as ``pretty/tag.html`` matches them.
    * **A scenario with no tags of its own and no feature tag contributes
      nothing.**  Five of the ten features declare no feature-level tag, and an
      untagged scenario omits the ``tags`` key rather than carrying an empty
      list, so an empty tag set is the ordinary case rather than an error.
    * **An unselected scenario is not a tag's scenario.**  It never ran, so the
      JVM emitted no tag object for it: a tag carried *only* by unselected
      scenarios therefore gets no row and no page at all.

    Backgrounds are not tagged elements and are never subjects here; the tag
    page still renders the Background occurrences belonging to the scenarios it
    lists, which is that template's own doing.

    Args:
        features: The features being emitted, from :func:`emitted_features`.

    Returns:
        An insertion-ordered mapping from tag name to the scenario elements
        that carry it.  The order is first appearance -- features in source
        order, a feature's own tags before its scenarios' -- so a rerun over
        the same input cannot reshuffle the emitted page set.
    """
    grouped: dict[str, list[JsonDict]] = {}
    for feature in features:
        feature_tags = _tag_names(feature.get("tags"))
        for element in _mappings(feature.get("elements")):
            if not _is_scenario(element) or not _is_selected(element):
                continue
            for name in feature_tags + _tag_names(element.get("tags")):
                subjects = grouped.setdefault(name, [])
                # A scenario carrying the same tag at both feature and
                # scenario level - the common case in this suite - is one
                # subject, not two.
                if not any(subject is element for subject in subjects):
                    subjects.append(element)
    return grouped


def tag_href_map(tag_names: Iterable[str]) -> dict[str, str]:
    """Map each tag name to its detail-page filename.

    Consumed by ``pretty/_element_tree.html``'s ``tags_block``, which renders a
    tag with an entry as a link and a tag without one as a plain chip, and by
    the tags overview for its rows.

    Args:
        tag_names: The tag names of the run, from :func:`collect_tags`.

    Returns:
        A mapping from tag name to page filename, in the iteration order of
        ``tag_names``.
    """
    return {name: tag_page_name(name) for name in tag_names}


# --------------------------------------------------------------------------- #
# The tags-overview rows.
#
# ``pretty/overview_tags.html`` is the one page with no derivation of its own,
# so its rows are built here.  Every rule below mirrors ``pretty/tag.html``'s
# tally, which computes the same numbers for the same tag from the same model:
# the overview row and the tag page's single row are two readings of one rule,
# and they were verified to agree cell for cell.
# --------------------------------------------------------------------------- #


def _tag_row(name: str, subjects: Sequence[JsonDict], href: str) -> JsonDict:
    """Build one tags-overview row.

    Args:
        name: The tag, leading ``@`` included, which is also the row's label.
        subjects: The selected scenario elements carrying the tag.
        href: The tag's detail-page filename, or ``""`` for no link.

    Returns:
        A mapping carrying exactly the keys ``pretty/_stats_table.html``
        reads: ``name``, ``href``, the six step counts, the three scenario
        counts, ``duration_ns`` and ``status``.  Counts and durations are raw
        integers and the status is a raw token, because formatting belongs to
        ``pretty/_macros.html`` and normalisation to
        ``partials/status_badge.html``.
    """
    counts = {
        "steps_passed": 0,
        "steps_failed": 0,
        "steps_skipped": 0,
        "steps_pending": 0,
        "steps_undefined": 0,
    }
    steps_total = 0
    scenarios_passed = 0
    scenarios_failed = 0
    duration_ns = 0
    observed: list[str] = []

    for element in subjects:
        tokens = _step_tokens(element)
        observed.extend(tokens)
        for token in tokens:
            steps_total += 1
            # A token with no column of its own - 'untested' and 'unknown' -
            # still counts towards Total, where it honestly belongs, and in
            # none of the five status columns: assigning it one would
            # fabricate a number the source cannot produce.
            key = f"steps_{token}"
            if key in counts:
                counts[key] += 1
        verdict = worst_status(tokens)
        # A scenario whose worst status is neither passed nor failed - an
        # undefined step, say - counts in neither column, which is what the
        # tag page's own tally does.
        if verdict == "passed":
            scenarios_passed += 1
        elif verdict == "failed":
            scenarios_failed += 1
        duration_ns += element_duration_ns(element)

    return {
        "name": name,
        "href": href,
        **counts,
        "steps_total": steps_total,
        "scenarios_passed": scenarios_passed,
        "scenarios_failed": scenarios_failed,
        "scenarios_total": len(subjects),
        "duration_ns": duration_ns,
        "status": worst_status(observed),
    }


def build_tag_rows(
    tags: Mapping[str, Sequence[JsonDict]],
    hrefs: Mapping[str, str] | None = None,
) -> list[JsonDict]:
    """Build every tags-overview row, in the order the tags were collected.

    Args:
        tags: Tag name to its selected scenario elements, from
            :func:`collect_tags`.
        hrefs: Tag name to detail-page filename, from :func:`tag_href_map`.
            A tag with no entry renders as plain text rather than as a link,
            which is the template's own behaviour for an absent href; the
            default computes the filename for each tag so a caller cannot
            accidentally produce a row that links nowhere.

    Returns:
        One row per tag.  An empty mapping yields an empty list, which the
        template renders as a complete page carrying "You have no tags in your
        cucumber report" -- the ordinary outcome for a run over the five
        features that declare no feature-level tag.
    """
    links = dict(hrefs) if hrefs is not None else tag_href_map(tags)
    return [
        _tag_row(name, subjects, links.get(name, "")) for name, subjects in tags.items()
    ]


def build_tag_totals(rows: Sequence[JsonDict]) -> JsonDict:
    """Sum the tags-overview rows for the statistics table's footer.

    The footer reads the nine counts and the duration, and its last two cells
    read ``features`` and ``features_passed`` -- the generator's own key names
    for "how many subjects does this table have, and how many of them passed".
    On this page the subject is a tag, so they carry the row count and the
    number of rows whose status is ``passed``, which is the same reading
    ``pretty/overview_features.html`` applies to its own rows.

    Args:
        rows: The rows from :func:`build_tag_rows`.

    Returns:
        A mapping carrying the nine counts, ``duration_ns``, ``features`` and
        ``features_passed``.  Every value is ``0`` for an empty row set, so the
        footer still renders.
    """
    totals: JsonDict = {
        key: sum(int(row.get(key, 0)) for row in rows) for key in _COUNT_KEYS
    }
    totals["duration_ns"] = sum(int(row.get("duration_ns", 0)) for row in rows)
    totals["features"] = len(rows)
    totals["features_passed"] = sum(1 for row in rows if row.get("status") == "passed")
    return totals


# --------------------------------------------------------------------------- #
# The build date
# --------------------------------------------------------------------------- #


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse one of the document's ISO-8601 timestamps, or answer ``None``.

    The collector emits exactly ``YYYY-MM-DDTHH:MM:SS.mmmZ``.  The trailing
    ``Z`` is accepted natively by :meth:`datetime.datetime.fromisoformat` on
    the interpreters this project supports, and is also substituted explicitly
    so that a document written by an older or hand-edited producer still
    parses.
    """
    text = _as_text(value)
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            return parsed
        # A naive timestamp is read as UTC, which is what the collector writes.
        return parsed.replace(tzinfo=timezone.utc)
    logger.warning("Cannot parse %r as a timestamp; using it verbatim", text)
    return None


def format_build_date(
    result_set: ResultSet | None = None,
    now: datetime | None = None,
) -> str:
    """Return the already-formatted Date cell of every page's build-info table.

    ``pretty/_layout.html`` documents ``build_date_display`` as arriving
    formatted, because locale, timezone and format decisions belong to this
    module and a template must not make them.  The shape is the reference's own
    -- ``07 Sep 2022, 15:39`` -- assembled from :data:`MONTH_ABBREVIATIONS`
    rather than from ``strftime('%b')`` so that a process running under a
    non-English locale (this suite deliberately drives one scenario with a
    French-locale browser environment) still produces the same text.

    The moment is taken from the document, in this order, so that two renders
    of one document agree: ``generated_at``, which is when the document was
    written and is the closest analogue of the generator's report date; then
    ``started_at``, the earliest scenario start; then the current time, which
    is the only case in which two renders of one input can differ.  Values are
    rendered in UTC, which is what the collector records and what keeps the
    output independent of the agent's timezone.

    Args:
        result_set: The merged result document, or ``None``.
        now: The moment to fall back to, for a caller -- a test -- that wants a
            fixed one.  Defaults to the current UTC time.

    Returns:
        The formatted date, e.g. ``"07 Sep 2022, 13:39"``.  A timestamp present
        in the document but unparseable is returned verbatim rather than
        discarded, because a reader is better served by an odd date than by
        none.

    Examples:
        >>> format_build_date({"generated_at": "2022-09-07T13:39:04.123Z"})
        '07 Sep 2022, 13:39'
        >>> format_build_date({"started_at": "2022-09-07T13:37:26.297Z"})
        '07 Sep 2022, 13:37'
        >>> format_build_date(None, now=datetime(2026, 1, 2, 3, 4,
        ...                                      tzinfo=timezone.utc))
        '02 Jan 2026, 03:04'
    """
    document = _as_mapping(result_set)
    raw: Any = None
    for key in ("generated_at", "started_at"):
        raw = document.get(key)
        parsed = _parse_timestamp(raw)
        if parsed is not None:
            return _format_moment(parsed)
        if _as_text(raw):
            # Present but unparseable: keep what the document says.
            return _as_text(raw)
    moment = now if now is not None else datetime.now(timezone.utc)
    return _format_moment(moment)


def _format_moment(moment: datetime) -> str:
    """Render one moment in the reference's ``dd Mon yyyy, HH:MM`` shape, in UTC."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    month = MONTH_ABBREVIATIONS[moment.month - 1]
    return (
        f"{moment.day:02d} {month} {moment.year:04d}, "
        f"{moment.hour:02d}:{moment.minute:02d}"
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def build_environment() -> Environment:
    """Build the template environment this writer renders through.

    A plain :class:`jinja2.Environment` rather than Flask's, because this
    writer runs inside a worker process that never builds an application:
    the framework's template helper and its application proxy both require an
    application context, and the pages are written to disk and opened over
    the file protocol straight from a CI workspace, where a framework-built
    static URL could not resolve anyway.

    The loader root is :func:`app.utils.paths.templates_dir`, which is what
    makes the templates' own ``pretty/_layout.html`` and
    ``partials/status_badge.html`` references resolve.  Autoescaping is on for
    every template regardless of extension: feature names in this suite carry
    quotation marks, periods and apostrophes, one of them opens with ``....``,
    and the reference generator escapes its own output too.

    Returns:
        A fresh environment.  Callers that render repeatedly may build one and
        pass it to :func:`render_pretty_pages`, which avoids re-reading the
        templates per page.
    """
    return Environment(
        loader=FileSystemLoader(str(templates_dir())),
        autoescape=True,
    )


def render_pretty_pages(
    result_set: ResultSet | None,
    project_name: str = DEFAULT_PROJECT_NAME,
    build_date: str | None = None,
    environment: Environment | None = None,
) -> dict[str, str]:
    """Render every page of the tree, keyed by its output filename.

    The pure half: it reads the model, renders the templates and **touches no
    file**, so a template fault surfaces before anything is written and a test
    can assert on the markup without a filesystem.

    The page set is the four overview pages, always -- the run's exit contract
    requires all four artifacts even when the tag expression selects nothing,
    so an empty document yields four complete pages rather than none -- plus
    one ``report-feature_<hash>.html`` per emitted feature and one
    ``report-tag_<hash>.html`` per tag of the run.

    Each template is given only what it needs, and every filename and link is
    computed here:

    * the features, steps and failures overviews receive the normalized
      feature list and the link maps, and derive their own rows by the rules
      their own docstrings take from the generator's bytecode -- deriving them
      a second time in Python would be a second source of truth for one
      number;
    * the tags overview receives the rows and totals built here, because it has
      no derivation of its own;
    * a feature page receives its feature and the tag links; a tag page
      receives its tag, the whole feature list -- it applies feature-tag
      propagation and the selected flag itself -- and the tag links;
    * every page receives ``project_name`` and ``build_date_display``.
      ``title_suffix`` is set by each page template, and ``active_page`` by the
      four overview templates only: the two detail pages deliberately mark no
      navigation item, as measured on the reference's marker-free detail pages.

    Args:
        result_set: The merged result document, or ``None`` for a run that
            produced nothing.
        project_name: The Project cell of the build-info table.
        build_date: The already-formatted Date cell.  Defaults to
            :func:`format_build_date` over ``result_set``, computed once so
            that every page of one render carries the same date.
        environment: An environment to render through; defaults to
            :func:`build_environment`.

    Returns:
        Output filename to HTML, in the order the pages are written: the four
        overviews in contract order, then the feature pages in source order,
        then the tag pages in first-appearance order.

    Raises:
        jinja2.TemplateError: If a template is missing, malformed or fails to
            render.  Deliberately not swallowed -- it is a genuine render
            fault, not a test outcome -- and because it is raised before any
            file is opened, a fault here leaves the previous tree untouched.
    """
    env = environment if environment is not None else build_environment()
    features = emitted_features(result_set)
    feature_links = feature_href_map(features)
    tags = collect_tags(features)
    tag_links = tag_href_map(tags)
    tag_rows = build_tag_rows(tags, tag_links)
    tag_totals = build_tag_totals(tag_rows)
    chrome = {
        "project_name": project_name,
        "build_date_display": (
            build_date if build_date is not None else format_build_date(result_set)
        ),
    }

    # Per-page context.  The keys are exactly what each template documents as
    # its render contract; nothing is passed that a template does not read.
    overview_context: dict[str, JsonDict] = {
        OVERVIEW_FEATURES_PAGE: {
            "features": features,
            "feature_hrefs": feature_links,
        },
        OVERVIEW_TAGS_PAGE: {
            "tags": tag_rows,
            "totals": tag_totals,
        },
        OVERVIEW_STEPS_PAGE: {
            "features": features,
        },
        OVERVIEW_FAILURES_PAGE: {
            "features": features,
            "feature_hrefs": feature_links,
            "tag_hrefs": tag_links,
        },
    }

    pages: dict[str, str] = {}
    for filename, template_name in OVERVIEW_TEMPLATES:
        template = env.get_template(template_name)
        pages[filename] = template.render(**chrome, **overview_context[filename])

    feature_template = env.get_template(FEATURE_TEMPLATE)
    for feature in features:
        uri = _as_text(feature.get("uri"))
        if not uri:
            # Already reported by feature_href_map: with no URI there is no
            # hash input and therefore no page name.  The feature still
            # appears as a row on the overviews.
            continue
        filename = feature_page_name(uri)
        if filename in pages:
            # Two features sharing one URI cannot come out of a correct merge,
            # which keys one feature object per path, so this is reported
            # rather than papered over with an invented disambiguator: the
            # generator has none either, since its jsonFileNo mechanism
            # applies only across separate input files.
            logger.warning(
                "Two features resolve to %s (uri %r); the later one is not written",
                filename,
                uri,
            )
            continue
        pages[filename] = feature_template.render(
            **chrome,
            feature=feature,
            tag_hrefs=tag_links,
        )

    tag_template = env.get_template(TAG_TEMPLATE)
    rows_by_tag = {row["name"]: row for row in tag_rows}
    for name, subjects in tags.items():
        pages[tag_links[name]] = tag_template.render(
            **chrome,
            tag=rows_by_tag[name],
            elements=_tag_page_elements(subjects, features, feature_links),
            tag_hrefs=tag_links,
        )
    return pages


def _tag_page_elements(
    subjects: Sequence[JsonDict],
    features: Sequence[JsonDict],
    feature_links: Mapping[str, str],
) -> list[JsonDict]:
    """Prepare one tag page's element list, in the shape the reference emits.

    ``pretty/tag.html`` accepts either the whole feature list, which it then
    groups feature by feature, or a flat element list.  The reference tag page
    is the flat shape: its ``report-tag_4059758862.html`` lists four scenario
    elements in one container, each opened with a ``Feature: <name>`` link row,
    and the word "Background" does not occur on it at all.  The flat form is
    also the honest one here, because the subjects were already selected by
    :func:`collect_tags` -- passing the feature list would have the template
    repeat that selection and could let the page's own tally drift from the
    row this module put on the tags overview.

    ``pretty/_element_tree.html``'s ``elements_block`` reads ``feature_href``
    and ``feature_name`` off each item it is handed, so each element is copied
    shallowly and the two keys added.  The copy matters: the model belongs to
    the caller and the other pages render the same objects, so nothing is
    mutated in place.  Unknown keys are ignored by every macro that reads an
    element, which is why this is an addition rather than a wrapper.

    Args:
        subjects: The tag's selected scenario elements, from
            :func:`collect_tags`.
        features: The emitted features, used to find the feature each subject
            came from -- by identity, because two features may share an id and
            a name.
        feature_links: The map from :func:`feature_href_map`.

    Returns:
        One shallow copy per subject, in document order, each carrying
        ``feature_href`` and ``feature_name``.  A subject whose feature has no
        page link keeps the feature's name, which the template renders as plain
        text rather than as a link that goes nowhere.
    """
    owner: dict[int, JsonDict] = {}
    for feature in features:
        for element in _mappings(feature.get("elements")):
            owner[id(element)] = feature

    prepared: list[JsonDict] = []
    for subject in subjects:
        feature = owner.get(id(subject), {})
        uri = _as_text(feature.get("uri"))
        item = dict(subject)
        item["feature_href"] = feature_links.get(uri, "")
        item["feature_name"] = _as_text(feature.get("name"))
        prepared.append(item)
    return prepared


# --------------------------------------------------------------------------- #
# Assets and writing
# --------------------------------------------------------------------------- #


def _copy_file(source: Path, destination: Path) -> Path:
    """Copy one asset byte for byte, creating its destination directory.

    :func:`shutil.copy2` rather than a read-and-write, because these are the
    reference generator's own minified files and fonts: they are copied, never
    regenerated, re-minified or re-encoded.  An existing file is overwritten in
    place; nothing is deleted.
    """
    ensure_dir(destination.parent)
    shutil.copy2(source, destination)
    return destination


def copy_pretty_assets(destination: Path | str) -> tuple[Path, ...]:
    """Copy the vendored asset set into the emitted tree.

    The impure half of the asset story.  Two sources feed the tree, and both
    are package-relative so they travel with an installed wheel:

    * :func:`app.utils.paths.vendor_dir` -- Bootstrap, jQuery, Chart.js,
      tablesorter, Moment, the two icon fonts and the favicon, whose ``css/``,
      ``js/``, ``fonts/`` and ``images/`` sub-directories map 1:1 onto the
      emitted tree.  The whole directory is walked rather than a hard-coded
      list copied, so the emitted set is the vendored set by construction.
      The vendored stylesheets request their fonts as ``url(../fonts/<name>)``,
      which resolves because ``fonts/`` sits beside ``css/``.
    * :func:`app.utils.paths.static_dir` -- the port's own ``css/main.css`` and
      ``js/report.js``, which ``pretty/_layout.html`` links tree-relative in
      addition to the vendored files.  Omitting them ships every page with two
      broken references, so they are copied here and their presence is
      verified below.

    Args:
        destination: The emitted tree's root -- the directory the pages
            themselves are written into, since every reference in a page is
            relative to the page.

    Returns:
        Every path written, sorted, so a caller can log or assert on the set.

    Raises:
        FileNotFoundError: If an asset an emitted page links by name is
            missing afterwards.  That is a broken installation rather than a
            test outcome: every page would ship a dead reference, so it is
            reported as the writer failure it is.  A missing *font* is warned
            about instead, because fonts are requested from the vendored CSS
            rather than from a page and the pages still render without them.
        OSError: If a directory cannot be created or a file cannot be copied.
    """
    root = ensure_dir(destination)
    written: list[Path] = []

    source_root = vendor_dir()
    # Sorted so the copy order - and therefore any log or error naming a file
    # - is the same on every run and every platform.
    for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
        written.append(_copy_file(source, root / source.relative_to(source_root)))

    static_root = static_dir()
    for relative_source, relative_destination in PORT_ASSETS:
        source = static_root.joinpath(*relative_source.split("/"))
        written.append(
            _copy_file(source, root.joinpath(*relative_destination.split("/")))
        )

    missing_links = [
        name
        for name in PAGE_LINKED_ASSETS
        if not root.joinpath(*name.split("/")).is_file()
    ]
    if missing_links:
        raise FileNotFoundError(
            "the generated report tree would link assets that were not copied: "
            + ", ".join(missing_links)
        )
    missing_fonts = [
        name
        for name in VENDORED_FONT_ASSETS
        if not root.joinpath(*name.split("/")).is_file()
    ]
    if missing_fonts:
        logger.warning(
            "Report tree %s is missing %d vendored font file(s): %s",
            root,
            len(missing_fonts),
            ", ".join(missing_fonts),
        )
    return tuple(sorted(written))


def write_pretty_reports(
    result_set: ResultSet | None,
    base: Path | str | None = None,
    directory: Path | str | None = None,
    project_name: str = DEFAULT_PROJECT_NAME,
    build_date: str | None = None,
    environment: Environment | None = None,
) -> Path:
    """Write the whole report tree, pages and assets, and return its directory.

    The composition: render everything first, so a template fault cannot leave
    a half-written tree; then create the directory and copy the assets, so a
    packaging fault surfaces before a page that would reference them is
    written; then write the pages in the order
    :func:`render_pretty_pages` produced them.

    The tree is overwritten **in place**.  Nothing is deleted -- not the
    directory, not a page from an earlier run:
    :mod:`app.utils.paths` creates directories and never removes them, and
    emptying the build-output directory belongs to ``app/cli.py``'s
    ``--clean`` step, which runs before the suite does.  A page from a previous
    run whose feature or tag has since disappeared is therefore left where it
    is, exactly as a generator writing into an uncleaned directory would leave
    it.

    Args:
        result_set: The merged result document, or ``None`` for a run that
            produced nothing -- the four overview pages are still written, per
            the exit contract.
        base: Directory to resolve the artifact path against, defaulting to the
            working directory, exactly as every :mod:`app.utils.paths`
            accessor does.  This is how a test writes into a temporary
            directory.
        directory: An explicit output directory, which overrides ``base``
            entirely, for a caller that already holds a path.
        project_name: The Project cell of the build-info table.
        build_date: The already-formatted Date cell; see
            :func:`format_build_date`.
        environment: An environment to render through; see
            :func:`build_environment`.

    Returns:
        The directory written.  With the default ``base`` it is
        :func:`app.utils.paths.pretty_reports_html_dir`, and the overview index
        inside it is exactly :func:`app.utils.paths.pretty_reports_index_path`
        -- the file served when a request names the artifact directory itself.

    Raises:
        jinja2.TemplateError: If a page cannot be rendered.  Raised before any
            file is touched, so the tree on disk is left as it was.
        FileNotFoundError: If a page-linked asset is missing; see
            :func:`copy_pretty_assets`.
        OSError: If the directory cannot be created or a file cannot be
            written.  Deliberately not swallowed: producing this artifact is
            the writer's contract with the run's exit table, whose
            writer-failure class names the failing writer on stderr and leaves
            the artifacts already written in place.  Because this writer emits
            many files, that is exactly what a fault part-way through the page
            loop produces, and it is intended.
    """
    pages = render_pretty_pages(
        result_set,
        project_name=project_name,
        build_date=build_date,
        environment=environment,
    )
    root = ensure_dir(pretty_reports_html_dir(base) if directory is None else directory)
    assets = copy_pretty_assets(root)

    for filename, html in pages.items():
        target = root / filename
        # newline="\n" so a page written on Windows is byte-identical to one
        # written on Linux: the structure of this artifact must not depend on
        # which side of the pipeline's isUnix() branch produced it.
        with open(target, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(html)
        logger.debug("Wrote %s", target)

    logger.info(
        "Wrote %d page(s) and %d asset(s) to %s", len(pages), len(assets), root
    )
    return root
