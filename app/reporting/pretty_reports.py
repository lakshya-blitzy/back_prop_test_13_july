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
   generator; :func:`emitted_features` therefore drops it -- and its feature
   with it once no test case is left -- and a tag carried only by unselected
   scenarios gets no page (:func:`collect_tags`).
3. **The tag set and the tag pages.**  Tag collection, tag ordering and each
   tag's detail-page name are this module's, and the rows those pages and
   ``pretty/overview_tags.html`` render come from the aggregation authority
   (:func:`build_tag_rows`, :func:`build_tag_totals`), so an overview row and
   the tag page it links are two readings of one calculation.
4. **Rendering and writing.**  :func:`iter_pretty_pages` renders and yields
   one page at a time; :func:`render_pretty_pages` is
   ``dict(iter_pretty_pages(...))`` and touches no file;
   :func:`copy_pretty_assets` copies the vendored asset set and validates the
   required manifest; :func:`write_pretty_reports` publishes the tree.
5. **This artifact's publication.**  The tree is built in a staging directory
   and swapped into place, so the published tree is a complete generation or
   the previous complete one -- never a mixture.  See *Publication* below.

What this module deliberately does *not* own
--------------------------------------------
* **Markup.**  All six page templates and their four helpers already exist
  under ``app/templates/pretty/``.
* **Aggregation.**  :mod:`app.reporting.aggregation` is the single normalised
  result model: every status, fold, count, duration, timestamp and selection
  rule is computed there and **read** here.  This module used to reimplement
  all of them and the copies had already drifted apart from the ones in
  ``app/reporting/html_report.py``, the Pretty templates and
  ``app/web/routes.py`` (``ambiguous`` folded onto Undefined on the features
  overview and nowhere else; hook statuses counted on some surfaces only;
  failed scenarios derived as *total minus passed* on one page and as *a
  literal failed token* on the next).  The names this module still exports for
  those calculations -- :func:`status_token`, :func:`worst_status`,
  :func:`element_status`, :func:`element_duration_ns`,
  :func:`emitted_features`, :func:`build_tag_rows`, :func:`build_tag_totals`
  and the status vocabulary -- are the authority's, re-exported or delegated to
  so that a consumer written against this module's surface keeps working while
  there is exactly one implementation underneath.
* **The tablesorter initialisation.**  ``pretty/_layout.html`` emits it
  exactly once per page.  A second copy must never be added here.
* **Emptying the build-output directory.**  That is ``app/cli.py``'s
  ``--clean`` step.  This writer replaces **its own** output directory and
  nothing else: no sibling artifact -- the JSON report, the rerun manifest,
  the self-contained page -- is read, moved or removed here, and the
  build-output directory itself is never emptied.
* **Status or duration presentation.**  ``pretty/_macros.html`` owns duration,
  count, percentage and timestamp formatting and
  ``partials/status_badge.html`` owns status normalisation, so raw nanosecond
  integers and raw status strings are passed through untouched.

The render contract this writer feeds
-------------------------------------
Every page receives ``project_name`` and ``build_date_display``;
``title_suffix`` is set by each page template and ``active_page`` by the four
overview templates only.  Beyond that chrome, one
:func:`app.reporting.aggregation.normalize_run` call produces everything the
pages show, and each page is handed the products it needs rather than a model
to re-derive:

============================ ===============================================
Page                         Context it receives
============================ ===============================================
the features overview       ``features`` (decorated), ``feature_hrefs``,
                             ``feature_rows`` and ``totals`` -- the
                             authority's per-feature statistics rows and
                             their footer sums -- and the same two values
                             under ``feature_stats`` and ``feature_totals``,
                             which are the names this template's own
                             override contract already declares and reads.
``overview-tags.html``       ``tags`` (the authority's tag rows) and
                             ``totals`` (their footer sums).
``overview-steps.html``      ``features`` (decorated).  Per-step-location
                             aggregation is this page's own and has no
                             authority equivalent.
``overview-failures.html``   ``features`` (decorated), ``feature_hrefs``,
                             ``tag_hrefs``.
``report-feature_*.html``    ``feature`` (decorated), ``feature_stats`` (that
                             feature's authority row), ``tag_hrefs``.
``report-tag_*.html``        ``tag`` (that tag's authority row),
                             ``elements`` (its decorated scenario elements,
                             each carrying ``feature_href`` and
                             ``feature_name``), ``tag_hrefs``.
============================ ===============================================

*Decorated* is :func:`app.reporting.aggregation.decorate_feature`: every
feature and every element carries ``status`` (the severity fold), ``verdict``
(the binary PrettyReports one), ``duration_ns``, ``duration_samples`` and
``stats``.  The templates are free to switch from deriving those values to
reading them; until they do, the rows and totals above are what stops the
overview and the detail pages from stating different numbers, because both
read one calculation.

Publication
-----------
The report tree :func:`app.utils.paths.pretty_reports_html_dir` names is a
**directory** artifact, so
"complete or untouched" cannot be had from one atomic file write.  It is had
from a staged swap instead:

1. a dot-prefixed sibling of the final directory is the staging tree -- a
   sibling so that the swap is a rename on one filesystem, dot-prefixed so
   that :func:`app.utils.paths.resolve_artifact` rejects it and no partial
   generation is ever reachable over HTTP;
2. the assets are copied into staging and validated, then the pages are
   written into it one at a time from :func:`iter_pretty_pages`, then the
   whole inventory -- every page-linked asset, all eleven fonts and every page
   the iterator produced -- is verified **before** anything is published;
3. the existing tree is renamed aside, staging is renamed into place, and the
   renamed-aside copy is deleted.  Two renames rather than one replace,
   because renaming a directory onto an existing directory fails on Windows
   and on POSIX alike, and AAP 0.8 requires Windows support;
4. on any failure the staging tree is removed and a tree already renamed
   aside is renamed back, so the previous complete generation survives; and no
   staging or renamed-aside directory outlives the call either way.

The published tree therefore holds exactly one detail page per current feature
and per current tag, with no page from an earlier run left behind -- which is
what the artifact contract means by "one detail page per feature and per tag"
and what ``--no-clean`` used to break.

Boundaries
----------
* Imports are limited to the standard library, :mod:`jinja2`,
  :mod:`app.reporting.aggregation` (every derived number),
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
  The **one** name derived here is the staging directory's, and it is derived
  here on purpose: it is a sibling of a path :mod:`app.utils.paths` owns, its
  leading dot is exactly what that module's own ``resolve_artifact`` rejects,
  and it exists only for the duration of a single :func:`write_pretty_reports`
  call, so it is this writer's private publication detail rather than an
  artifact location.

Determinism
-----------
Byte stability across runs is impossible (durations, timestamps and screenshot
bytes vary by construction), so what is guaranteed is **structure**: features
in the model's own source order, scenarios in line order, Background
occurrences left where they are, tag pages in first-appearance order, and the
same page set for the same input.  Nothing here sorts: the publisher's
``sortingMethod: 'ALPHABETICAL'`` (``Jenkins:15``) is a display option of its
own and imposes nothing on the artifacts.  **Two renders of one document are
identical, including the chrome**: the build date comes from the document's own
``generated_at`` or ``started_at`` and is empty when it carries neither, so no
clock reaches a page, and the project name likewise comes from the document or
from the caller and is never a literal this module supplies.

Failure behaviour
-----------------
A **test outcome never raises**: failures, undefined steps, skipped steps, an
empty selection and an absent tag set are all data that render.  Malformed
model input is tolerated defensively rather than raised on, because a report is
what a reader turns to when a run has gone wrong.  Only genuine render, asset
or I/O faults propagate (:class:`jinja2.TemplateError`,
:class:`FileNotFoundError`, :class:`OSError`), becoming the command line's
writer-failure exit class, which names this writer on stderr.  Nothing
half-written is left behind when they do: the fault happens inside the staging
tree, that tree is removed, and the published tree is the previous complete
generation -- or, on a first run, absent.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, FileSystemLoader

# The aggregation authority.  Names imported here are re-exported deliberately:
# this module's published surface predates the authority, so a consumer that
# imports ``status_token`` or ``element_status`` from here keeps working while
# there is exactly one implementation of each underneath (see the module
# docstring's "Aggregation" bullet).
from app.reporting.aggregation import (
    KNOWN_STATUSES,
    STATUS_PRECEDENCE,
    UNKNOWN_STATUS,
    as_mapping,
    as_text,
    build_row_totals,
    element_duration_ns,
    element_status,
    is_scenario_element,
    is_selected,
    mappings,
    normalize_run,
    parse_timestamp,
    selected_features,
    status_token,
    tag_row,
    worst_status,
)
from app.reporting.aggregation import (
    build_tag_rows as _aggregate_tag_rows,
)
from app.reporting.events import JsonDict, ResultSet
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
    "REQUIRED_ASSETS",
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
    "iter_pretty_pages",
    "java_hash_code",
    "local_page_href",
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
#: ``url(../fonts/<name>)``.  All eleven are **mandatory**, exactly as the
#: page-linked assets above are: the stylesheets this writer copies request
#: them by name, so a tree missing one links a file that is not there, and a
#: published artifact that links absent files is broken rather than merely
#: unstyled.  :func:`copy_pretty_assets` therefore raises for a missing font,
#: and it raises before publication, so the previous complete tree stands.
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

#: The asset manifest a published tree must satisfy in full: everything a page
#: links by name and every font the copied stylesheets request.  One name for
#: the whole requirement, checked by :func:`copy_pretty_assets` after the copy
#: and again over the staging tree before the swap, so "the tree references a
#: file that is not there" cannot become a published artifact.
REQUIRED_ASSETS: Final[tuple[str, ...]] = PAGE_LINKED_ASSETS + VENDORED_FONT_ASSETS

# --------------------------------------------------------------------------- #
# Status vocabulary.  :data:`KNOWN_STATUSES`, :data:`UNKNOWN_STATUS` and
# :data:`STATUS_PRECEDENCE` are imported from
# :mod:`app.reporting.aggregation` and re-exported unchanged: they were
# declared here as well until the two copies became two chances to disagree,
# and the authority is where ``partials/status_badge.html``'s vocabulary and
# ``pretty/_element_tree.html``'s precedence are now stated once.  The nine
# statistics-table count keys live there too, as ``COUNT_KEYS``, and are read
# by :func:`build_tag_totals` through the authority's own footer sum.
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Page identity
# --------------------------------------------------------------------------- #

#: The default Project cell of every page's build-info table, and it is
#: **deliberately empty**.  A report states what the run recorded; a project
#: name this module supplied from a literal would be metadata the result
#: document does not carry, presented in a metadata table as though it did --
#: and the reference's own value for the same absence is the generator's
#: placeholder text, not a name.  So the default is the empty string, the
#: document's ``project_name`` key is read when it has one
#: (:func:`render_pretty_pages`), and an explicit argument from a caller that
#: genuinely knows the name still wins over both.  ``pretty/_layout.html``
#: renders an absent value as an empty cell, which is the honest answer.
DEFAULT_PROJECT_NAME: Final[str] = ""

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
# The link allowlist.
#
# Every page of this tree links only to another page of this tree, and every
# one of those names is computed above.  A destination that reaches a template
# from the result document is therefore never a destination: the document is
# produced by a run, and a run's JSON is data this writer reads rather than
# code it trusts.  Autoescaping does not help here -- it escapes HTML, and
# ``javascript:alert(9)`` contains not one character HTML escaping touches --
# so the check is an allowlist of the names the writer actually emits and
# nothing else.
# --------------------------------------------------------------------------- #

#: The four fixed navigation targets, as a set, built from the same constants
#: the writer names its files with so the allowlist cannot drift from them.
_FIXED_PAGE_NAMES: Final[frozenset[str]] = frozenset(
    {
        OVERVIEW_FEATURES_PAGE,
        OVERVIEW_TAGS_PAGE,
        OVERVIEW_STEPS_PAGE,
        OVERVIEW_FAILURES_PAGE,
    }
)

#: The two detail-page shapes: a fixed prefix, an unsigned decimal hash and the
#: suffix.  Assembled from :data:`FEATURE_PAGE_PREFIX`,
#: :data:`TAG_PAGE_PREFIX` and :data:`PAGE_SUFFIX` for the same reason, and
#: anchored at both ends.  ``[0-9]`` rather than ``\d``, which in Python also
#: matches the decimal digits of other scripts -- a filename this writer cannot
#: produce.
_DETAIL_PAGE_PATTERN: Final[re.Pattern[str]] = re.compile(
    "(?:{feature}|{tag})[0-9]+{suffix}".format(
        feature=re.escape(FEATURE_PAGE_PREFIX),
        tag=re.escape(TAG_PAGE_PREFIX),
        suffix=re.escape(PAGE_SUFFIX),
    )
)


def local_page_href(candidate: Any) -> str:
    """Return ``candidate`` when it names a page of this tree, else ``""``.

    The single validation point for every dynamic ``href`` the six page
    templates emit.  It is registered as a Jinja global by
    :func:`build_environment`, which is the only environment that renders
    ``pretty/*``, so every link sink in the folder reaches this one
    implementation rather than repeating a check four ways.

    **What is accepted** is exactly what this module writes: the four fixed
    overview filenames, and a detail page named by
    :func:`feature_page_name` or :func:`tag_page_name`.  Everything else is
    rejected, and the caller renders its label as plain text -- never as a link
    to nowhere and never as an empty anchor.  Rejected outright, therefore:

    * any scheme at all, ``javascript:`` and ``data:`` included, which
      autoescaping does not neutralise;
    * any absolute, protocol-relative, parent-relative or nested path, so no
      destination can leave the emitted tree;
    * any query or fragment, which no page of this tree takes;
    * whitespace, control characters, a non-string and an empty value.

    A non-empty value that is rejected is logged at warning level: the only
    way one can arrive is a result document carrying a destination the writer
    did not compute, and that is worth a line in the build log.  An empty or
    absent value is silent -- a feature with no URI has no page, which is a
    normal state this writer reports separately.

    Args:
        candidate: The value a template holds, of any type.  A
            :class:`jinja2.Undefined` from an absent model key is a non-string
            and is answered with ``""`` rather than raising, which is what lets
            the templates render under a strict undefined policy.

    Returns:
        The candidate unchanged when it is one of this tree's page names,
        otherwise the empty string.

    Examples:
        >>> local_page_href(OVERVIEW_FEATURES_PAGE) == OVERVIEW_FEATURES_PAGE
        True
        >>> local_page_href("report-feature_1364259633.html")
        'report-feature_1364259633.html'
        >>> local_page_href("report-tag_4059758862.html")
        'report-tag_4059758862.html'
        >>> local_page_href("javascript:alert(9)")
        ''
        >>> local_page_href("http://evil.example/x.html")
        ''
        >>> local_page_href("/etc/passwd")
        ''
        >>> local_page_href("../../etc/passwd")
        ''
        >>> local_page_href("report-feature_1364259633.html/../../x")
        ''
        >>> local_page_href(OVERVIEW_FEATURES_PAGE + "?a=b#z")
        ''
        >>> local_page_href("report-feature_.html")
        ''
        >>> local_page_href(None)
        ''
        >>> local_page_href("")
        ''
    """
    if not isinstance(candidate, str) or not candidate:
        return ""
    if candidate in _FIXED_PAGE_NAMES:
        return candidate
    # fullmatch, so nothing may precede or follow the name -- a path, a query
    # and a fragment are all "something following".
    if _DETAIL_PAGE_PATTERN.fullmatch(candidate):
        return candidate
    logger.warning(
        "Rejected %r as a page destination: it is not a page this writer emits",
        candidate,
    )
    return ""


# --------------------------------------------------------------------------- #
# What the tree contains: features, links and tags.
#
# Reading the model is not this module's work any more.  The coercions
# (``as_mapping``, ``mappings``, ``as_text``), the predicates (``is_selected``,
# ``is_scenario_element``) and every derived number (:func:`status_token`,
# :func:`worst_status`, :func:`element_status`, :func:`element_duration_ns`)
# come from :mod:`app.reporting.aggregation`.  They were duplicated here, and
# the copies had already drifted: the element status computed here ignored
# hooks while the features overview's own derivation folded them in, so one
# surface called a scenario with a failed after-hook passed and the next called
# it failed.  The authority's ``element_status`` folds steps *and* both hook
# groups, which is ``Element.calculateElementStatus``, and that is now the only
# answer this module can give.
# --------------------------------------------------------------------------- #


def emitted_features(result_set: ResultSet | None) -> list[JsonDict]:
    """Return the features that get a row and a detail page, in source order.

    Delegates to :func:`app.reporting.aggregation.selected_features`, which is
    the one place the selection rule lives.

    **This is a deliberate behaviour change from the rule this function used to
    apply**, and the change is the point: the old rule kept a feature when
    *any* of its elements was selected and then kept the *unselected* elements
    inside it, so a page could show a scenario that never ran, and a feature's
    row could count steps that the merged JSON report does not carry.  The
    authority applies ``app/reporting/cucumber_json.py``'s rule instead -- drop
    a Background-occurrence-plus-scenario unit whose members carry
    ``"selected": False``, then drop a feature left with no test case at all --
    so the JSON artifact, both HTML artifacts and the viewer describe the same
    run.  Under the default ``@Smoke`` filter that is what reduces the suite's
    ten features to the one the reference artifact carries.

    Nothing is sorted and nothing is mutated: the model's order is source order
    and it is preserved, and a feature that loses a unit is copied rather than
    edited.

    Args:
        result_set: The merged result document, or ``None`` for a run that
            produced nothing -- the artifacts are still written in that case,
            so ``None`` yields an empty list rather than an error.

    Returns:
        The feature mappings to render, in document order.
    """
    return selected_features(result_set)


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
        uri = as_text(feature.get("uri"))
        if not uri:
            # No URI, no hash input, and therefore no page: the feature still
            # renders as a row, without a link.
            logger.warning(
                "Feature %r carries no uri, so it gets no detail page link",
                as_text(feature.get("name")),
            )
            continue
        href = feature_page_name(uri)
        for key in (
            uri,
            as_text(feature.get("path")),
            as_text(feature.get("id")),
            as_text(feature.get("name")),
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
        name = as_text(item.get("name")) if isinstance(item, dict) else as_text(item)
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
    # Identity of the subjects already recorded for each tag, so the dedup
    # below is a hash lookup rather than a scan of a list that grows with every
    # scenario the tag carries -- which was quadratic in the size of the
    # largest tag.  ``id()`` is safe as the key because ``features`` is alive
    # for as long as the returned mapping is: every subject is an element of a
    # feature the caller still holds, so no identity can be recycled while this
    # set is in use.  The ordered list is what the caller reads, and it is
    # appended to in exactly the order it was before.
    seen: dict[str, set[int]] = {}
    for feature in features:
        feature_tags = _tag_names(feature.get("tags"))
        for element in mappings(feature.get("elements")):
            if not is_scenario_element(element) or not is_selected(element):
                continue
            for name in feature_tags + _tag_names(element.get("tags")):
                subjects = grouped.setdefault(name, [])
                recorded = seen.setdefault(name, set())
                # A scenario carrying the same tag at both feature and
                # scenario level - the common case in this suite - is one
                # subject, not two.
                if id(element) not in recorded:
                    recorded.add(id(element))
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
# so its rows are built here -- by the aggregation authority, which is also
# what ``pretty/tag.html`` renders for the same tag, so the overview row and
# the tag page's single row are two readings of one calculation rather than two
# calculations that used to be checked against each other by hand.
#
# The tally rules are ``net.masterthought:cucumber-reporting:5.6.1``'s, and
# every one of them is a rule the arithmetic that used to live here got wrong:
#
#   * ``ambiguous`` counts in the **Undefined** column.  ``StatusDeserializer``
#     holds ``UNKNOWN_STATUSES = ["ambiguous"]`` and rewrites it to
#     ``UNDEFINED`` before any counting happens.  It keeps its own severity
#     rank for grading; only the counters fold it.
#   * **Hooks take part in a scenario's verdict.**
#     ``Element.calculateElementStatus`` folds ``stepsStatus`` with
#     ``beforeStatus`` and ``afterStatus``, so a scenario whose steps all
#     passed but whose after-hook failed is not a passed scenario.  Hooks are
#     still never counted as steps and their durations are never added --
#     ``TagObject.addElement`` sums ``Step.getDuration()`` alone.
#   * **A scenario counts as passed only when its whole verdict is passed**,
#     and ``scenarios_failed`` is ``scenarios_total - scenarios_passed``:
#     ``TagObject.getFailedScenarios()`` counts the elements the counter did
#     not record as ``PASSED``, so an undefined, pending, skipped or
#     unrecognised outcome is a failed scenario there and not a scenario in
#     neither column.
#   * A step status with no column of its own -- ``untested`` and an
#     unrecognised token -- counts towards Total, where it honestly belongs,
#     and towards none of the five columns.
# --------------------------------------------------------------------------- #


def _tag_row(name: str, subjects: Sequence[JsonDict], href: str) -> JsonDict:
    """Build one tags-overview row.

    A delegation to :func:`app.reporting.aggregation.tag_row`, kept because
    this module's own callers and its documentation are written in terms of a
    tag's *subjects*.  The arithmetic is the authority's, which is what
    reproduces the four 5.6.1 rules stated above -- the same rules the
    features overview applies to a feature row, so a tag row and a feature row
    can no longer grade one scenario differently.

    Args:
        name: The tag, leading ``@`` included, which is also the row's label.
        subjects: The selected scenario elements carrying the tag.
        href: The tag's detail-page filename, or ``""`` for no link.

    Returns:
        A mapping carrying exactly the keys ``pretty/_stats_table.html``
        reads: ``name``, ``href``, the six step counts, the three scenario
        counts, ``duration_ns`` and ``status`` -- plus ``duration_samples``,
        which the authority adds so that a genuinely zero duration and a
        duration built from no sample at all stay distinguishable, and which
        the template ignores.  Counts and durations are raw integers and the
        status is a raw token, because formatting belongs to
        ``pretty/_macros.html`` and normalisation to
        ``partials/status_badge.html``.
    """
    return tag_row(name, subjects, href)


def build_tag_rows(
    tags: Mapping[str, Sequence[JsonDict]],
    hrefs: Mapping[str, str] | None = None,
) -> list[JsonDict]:
    """Build every tags-overview row, in the order the tags were collected.

    The rows themselves are :func:`app.reporting.aggregation.build_tag_rows`'s;
    what this function adds, and the only reason it is not that function, is
    the link default below, because the detail-page name is this module's to
    compute and the authority resolves no destination.

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
    return _aggregate_tag_rows(tags, links)


def build_tag_totals(rows: Sequence[JsonDict]) -> JsonDict:
    """Sum the tags-overview rows for the statistics table's footer.

    The footer reads the nine counts and the duration, and its last two cells
    read ``features`` and ``features_passed`` -- the generator's own key names
    for "how many subjects does this table have, and how many of them passed".
    On this page the subject is a tag, so they carry the row count and the
    number of rows whose status is ``passed``, which is the same reading
    ``pretty/overview_features.html`` applies to its own rows.

    A delegation to :func:`app.reporting.aggregation.build_row_totals`, which
    is the one footer sum: the features overview's footer and this one are the
    same arithmetic over different rows, and computing them separately is how
    one page came to report a total the other could not reproduce.

    Args:
        rows: The rows from :func:`build_tag_rows`.

    Returns:
        A mapping carrying the nine counts, ``duration_ns``, ``features`` and
        ``features_passed``.  Every value is ``0`` for an empty row set, so the
        footer still renders.
    """
    return build_row_totals(rows)


# --------------------------------------------------------------------------- #
# The build date
# --------------------------------------------------------------------------- #


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
    written and is the closest analogue of the generator's report date, then
    ``started_at``, the earliest scenario start.  Values are rendered in UTC,
    which is what the collector records and what keeps the output independent
    of the agent's timezone.

    **A document carrying neither answers the empty string.**  There is no
    clock fallback: the render-time clock is when somebody rendered the
    report, not when the run happened, and printing it in a metadata table
    states a fact the document does not carry -- the same reason
    :data:`DEFAULT_PROJECT_NAME` is empty.  ``pretty/_layout.html`` renders the
    empty value as an empty cell, with no invented placeholder in it.  In
    normal operation the case does not arise: the collector always writes
    ``generated_at`` before the writers run, so a real run's date is
    result-backed.  A caller that legitimately has a moment of its own -- a
    test pinning the output, a tool re-rendering an old document against a
    known time -- passes ``now`` and gets it formatted.

    Args:
        result_set: The merged result document, or ``None``.
        now: An explicit moment to use when the document carries no usable
            timestamp.  Defaults to ``None``, which yields ``""``.

    Returns:
        The formatted date, e.g. ``"07 Sep 2022, 13:39"``; ``""`` when the
        document carries no timestamp and no ``now`` was supplied.  A timestamp
        present in the document but unparseable is returned verbatim rather
        than discarded, because a reader is better served by an odd date than
        by none.

    Examples:
        >>> format_build_date({"generated_at": "2022-09-07T13:39:04.123Z"})
        '07 Sep 2022, 13:39'
        >>> format_build_date({"started_at": "2022-09-07T13:37:26.297Z"})
        '07 Sep 2022, 13:37'
        >>> format_build_date({"generated_at": "not a timestamp"})
        'not a timestamp'
        >>> format_build_date(None, now=datetime(2026, 1, 2, 3, 4,
        ...                                      tzinfo=timezone.utc))
        '02 Jan 2026, 03:04'
        >>> format_build_date(None)
        ''
        >>> format_build_date({"features": []})
        ''
    """
    document = as_mapping(result_set)
    raw: Any = None
    for key in ("generated_at", "started_at"):
        raw = document.get(key)
        # The authority's parser, so the instant behind this page's Date cell
        # and the instant behind the run's start_timestamp are read by one
        # rule.  It answers None for anything it cannot parse, which is what
        # the branch below turns into the verbatim fallback.
        parsed = parse_timestamp(raw)
        if parsed is not None:
            return _format_moment(parsed)
        if as_text(raw):
            # Present but unparseable: keep what the document says, because a
            # reader is better served by an odd date than by none.
            logger.warning(
                "Cannot parse %s %r as a timestamp; using it verbatim",
                key,
                as_text(raw),
            )
            return as_text(raw)
    # No usable timestamp.  An explicit moment is formatted; otherwise the cell
    # stays empty rather than reporting the render-time clock as the run's.
    return _format_moment(now) if now is not None else ""


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

    :func:`local_page_href` is installed as a **global**, not a filter, and
    installing it here is what puts one link allowlist behind every sink in
    the folder.  A global is reachable from a macro imported with the plain
    ``{% import %}`` form, which passes no context -- and every helper in
    ``app/templates/pretty/`` is imported that way on purpose -- whereas a
    context-dependent lookup would not be.  Autoescaping protects HTML and not
    URL schemes, so this is the only thing standing between a crafted
    destination in the result document and an active ``href``; see that
    function for what it accepts.

    Returns:
        A fresh environment.  Callers that render repeatedly may build one and
        pass it to :func:`render_pretty_pages`, which avoids re-reading the
        templates per page.
    """
    environment = Environment(
        loader=FileSystemLoader(str(templates_dir())),
        autoescape=True,
    )
    environment.globals["local_page_href"] = local_page_href
    return environment


def _element_owner_map(features: Sequence[JsonDict]) -> dict[int, JsonDict]:
    """Map each element's identity to the feature it belongs to.

    Built **once** for a whole render and handed to every tag page, because
    rebuilding it per tag walked every element of every feature again for each
    tag of the run.  Identity rather than a key, because two features in this
    suite may share an id and a name -- Contact with Inventory and Login with
    Notes -- so a name lookup would attribute a scenario to the wrong feature's
    page.

    Args:
        features: The decorated features the pages are rendered from.  It must
            be that very list: the map keys are ``id()`` values, so a map built
            over the undecorated model would miss every decorated element, and
            the list must stay alive for as long as the map is used, which it
            does -- the caller holds it for the whole render.

    Returns:
        A mapping from ``id(element)`` to the element's feature.
    """
    owner: dict[int, JsonDict] = {}
    for feature in features:
        for element in mappings(feature.get("elements")):
            owner[id(element)] = feature
    return owner


def _tag_page_elements(
    subjects: Sequence[JsonDict],
    owner: Mapping[int, JsonDict],
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
            :func:`collect_tags` over the decorated features.
        owner: The element-to-feature map from :func:`_element_owner_map`,
            built once per render over those same features.
        feature_links: The map from :func:`feature_href_map`.

    Returns:
        One shallow copy per subject, in document order, each carrying
        ``feature_href`` and ``feature_name``.  A subject whose feature has no
        page link keeps the feature's name, which the template renders as plain
        text rather than as a link that goes nowhere.
    """
    prepared: list[JsonDict] = []
    for subject in subjects:
        feature = owner.get(id(subject), {})
        uri = as_text(feature.get("uri"))
        item = dict(subject)
        item["feature_href"] = feature_links.get(uri, "")
        item["feature_name"] = as_text(feature.get("name"))
        prepared.append(item)
    return prepared


def iter_pretty_pages(
    result_set: ResultSet | None,
    project_name: str = DEFAULT_PROJECT_NAME,
    build_date: str | None = None,
    environment: Environment | None = None,
) -> Iterator[tuple[str, str]]:
    """Render the tree one page at a time, in the order the pages are written.

    The streaming form of :func:`render_pretty_pages`, and the form
    :func:`write_pretty_reports` uses.  One page string exists at a time: the
    shared maps, rows and statistics are built once up front and kept, each
    page is rendered, yielded and then dropped by the consumer before the next
    is rendered.  That matters on a run with failures, because a failed
    scenario carries its base64 screenshot and its traceback and appears on its
    feature page, on the failures overview and on every tag page its tag
    reaches -- holding all of those page strings at once duplicated those bytes
    as many times over.

    The page order is the artifact contract's own and is what makes the
    collision behaviour below deterministic: the four overviews, then the
    feature pages in source order, then the tag pages in first-appearance
    order.  The four overviews are always yielded, even for an empty document,
    because the run's exit contract requires all four artifacts even when the
    tag expression selects nothing.

    **A duplicate filename is yielded twice, deliberately.**  Two distinct
    feature URIs can hash to one detail-page name, and the reference generator
    writes its pages sequentially, so the later page overwrites the earlier and
    the later one is what the tree ends up holding.  This iterator reproduces
    that: it warns, naming the filename and both subjects, and yields the later
    page anyway.  A consumer that builds a mapping gets last-write-wins for
    free, and a consumer that writes as it goes gets the same bytes on disk.

    **The build-info metadata is the document's or the caller's, never this
    module's.**  Both cells are rendered only from what is present in, or
    derivable from, the result document unless a caller states otherwise: an
    empty ``project_name`` is answered from the document's own
    ``project_name`` key and left empty when it has none, and ``build_date``
    defaults to :func:`format_build_date`, which is empty for a document with
    no timestamp.  ``pretty/_layout.html`` renders either absence as an empty
    cell, so the table shows fewer facts rather than invented ones.

    Args:
        result_set: The merged result document, or ``None`` for a run that
            produced nothing.
        project_name: The Project cell of the build-info table.  Defaults to
            :data:`DEFAULT_PROJECT_NAME`, which is empty and makes the
            document's own ``project_name`` key the source.
        build_date: The already-formatted Date cell.  Defaults to
            :func:`format_build_date` over ``result_set``, computed once so
            that every page of one render carries the same date.
        environment: An environment to render through; defaults to
            :func:`build_environment`.

    Yields:
        ``(output filename, HTML)`` pairs, in the order above.  Nothing is
        written and no directory is touched.

    Raises:
        jinja2.TemplateError: If a template is missing, malformed or fails to
            render.  Deliberately not swallowed -- it is a genuine render
            fault, not a test outcome.
    """
    env = environment if environment is not None else build_environment()

    # One aggregation for the whole render.  ``feature_href_map`` runs over the
    # selection-filtered model first because the authority needs the link map
    # to put an href on each feature row; the rows and the decorated features
    # it returns are then what every page below reads, so no page derives a
    # number of its own.
    feature_links = feature_href_map(emitted_features(result_set))
    run = normalize_run(result_set, feature_links)
    # Every page context below derives from this one list -- the four
    # overviews, each feature page and each tag page -- and the aggregate it
    # comes from has already put every attachment through the embedding
    # contract in :func:`app.reporting.aggregation.decorate_element`, so no
    # template of this tree can be reached by an attachment that was not
    # validated.  Copies, never mutation: this writer is one of four fed from
    # a single merged result set, and the aggregate behind it is immutable.
    features = list(run.features)
    feature_rows = list(run.feature_rows)

    tags = collect_tags(features)
    tag_links = tag_href_map(tags)
    tag_rows = build_tag_rows(tags, tag_links)
    tag_totals = build_tag_totals(tag_rows)

    chrome = {
        # The build-info metadata, and every value in it is either the
        # caller's or the document's.  An empty project_name argument -- which
        # is the default -- is answered from the document's own project_name
        # key, and stays empty when it has none: the alternative is a name
        # printed in a metadata table that no part of the run recorded.
        "project_name": (
            project_name
            or as_text(as_mapping(result_set).get("project_name"))
        ),
        "build_date_display": (
            build_date if build_date is not None else format_build_date(result_set)
        ),
    }

    # Per-page context.  Each template gets the aggregate products it reads:
    # ``feature_rows``/``totals`` are the authority's names and
    # ``feature_stats``/``feature_totals`` the override names
    # ``pretty/overview_features.html`` already documents and reads, so the
    # page renders the authority's numbers today and can switch to the
    # canonical names without this writer changing again.  Nothing that was
    # passed before has been removed or renamed.
    overview_context: dict[str, JsonDict] = {
        OVERVIEW_FEATURES_PAGE: {
            "features": features,
            "feature_hrefs": feature_links,
            "feature_rows": feature_rows,
            "totals": run.totals,
            "feature_stats": feature_rows,
            "feature_totals": run.totals,
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

    for filename, template_name in OVERVIEW_TEMPLATES:
        template = env.get_template(template_name)
        yield filename, template.render(**chrome, **overview_context[filename])

    # What produced each detail name so far, so a collision can be reported
    # with both subjects.  Names only: this is the one thing retained across
    # the detail pages, and it costs a few dozen bytes per page rather than a
    # page string.
    produced: dict[str, str] = {}

    feature_template = env.get_template(FEATURE_TEMPLATE)
    for feature, row in zip(features, feature_rows, strict=True):
        uri = as_text(feature.get("uri"))
        if not uri:
            # Already reported by feature_href_map: with no URI there is no
            # hash input and therefore no page name.  The feature still
            # appears as a row on the overviews.
            continue
        filename = feature_page_name(uri)
        previous = produced.get(filename)
        if previous is not None:
            # Two distinct URIs hashing to one name.  The generator writes its
            # pages sequentially and has no disambiguator either -- its
            # jsonFileNo mechanism applies only across separate input files --
            # so the later page wins, here as there, and the collision is
            # reported rather than silently resolved.
            logger.warning(
                "%s is the detail page of both %r and %r; the later page wins",
                filename,
                previous,
                uri,
            )
        produced[filename] = uri
        yield (
            filename,
            feature_template.render(
                **chrome,
                feature=feature,
                feature_stats=row,
                tag_hrefs=tag_links,
            ),
        )

    tag_template = env.get_template(TAG_TEMPLATE)
    owner = _element_owner_map(features)
    rows_by_tag = {row["name"]: row for row in tag_rows}
    for name, subjects in tags.items():
        filename = tag_links[name]
        previous = produced.get(filename)
        if previous is not None:
            logger.warning(
                "%s is the detail page of both %r and %r; the later page wins",
                filename,
                previous,
                name,
            )
        produced[filename] = name
        yield (
            filename,
            tag_template.render(
                **chrome,
                tag=rows_by_tag[name],
                elements=_tag_page_elements(subjects, owner, feature_links),
                tag_hrefs=tag_links,
            ),
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
    can assert on the markup without a filesystem.  It is exactly
    ``dict(iter_pretty_pages(...))``, which is also where the detail-name
    collision rule comes from: building a mapping keeps the last value for a
    repeated key, so the later of two colliding pages is the one returned,
    matching both the reference generator and what
    :func:`write_pretty_reports` leaves on disk.

    The page set is the four overview pages, always -- the run's exit contract
    requires all four artifacts even when the tag expression selects nothing,
    so an empty document yields four complete pages rather than none -- plus
    one ``report-feature_<hash>.html`` per emitted feature and one
    ``report-tag_<hash>.html`` per tag of the run.

    Each template is given only what it needs, every filename and link is
    computed here, and every number comes from one
    :func:`app.reporting.aggregation.normalize_run` call; the module docstring
    tabulates the resulting context page by page.

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
    return dict(
        iter_pretty_pages(
            result_set,
            project_name=project_name,
            build_date=build_date,
            environment=environment,
        )
    )


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

    The copy is then held to :data:`REQUIRED_ASSETS` in full, **fonts
    included**.  A font is requested from a stylesheet rather than from a page,
    which used to be the argument for warning about a missing one and
    reporting success anyway; it is the wrong conclusion, because the
    stylesheet doing the requesting is a file this function itself copied into
    the tree, so a missing font is this writer publishing a reference to
    something it knows is not there.  All eleven are mandatory and their
    absence fails the write.

    Args:
        destination: The emitted tree's root -- the directory the pages
            themselves are written into, since every reference in a page is
            relative to the page.  During a publication this is the staging
            tree, so a missing asset is discovered before anything is swapped
            into place.

    Returns:
        Every path written, sorted, so a caller can log or assert on the set.

    Raises:
        FileNotFoundError: If any member of :data:`REQUIRED_ASSETS` is missing
            afterwards -- every asset a page links by name **and** all eleven
            fonts the copied stylesheets request.  That is a broken
            installation rather than a test outcome: the published tree would
            reference files that are not there, so it is reported as the writer
            failure it is, and the message names every missing file so the
            cause is in the log rather than in a reader's browser console.
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

    _require_assets(root)
    return tuple(sorted(written))


def _require_assets(root: Path) -> None:
    """Verify that every required asset is present under ``root``.

    Called after the copy and again over the staging tree before publication,
    because the two questions are different: the first says the copy did its
    job, the second says nothing has gone missing between the copy and the
    swap.

    Args:
        root: The tree to check -- the staging tree during a publication.

    Raises:
        FileNotFoundError: If any member of :data:`REQUIRED_ASSETS` is absent
            or is not a file, naming every one of them.
    """
    missing = [
        name
        for name in REQUIRED_ASSETS
        if not root.joinpath(*name.split("/")).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"the generated report tree {root} would reference "
            f"{len(missing)} asset(s) that are not there: " + ", ".join(missing)
        )


#: Infixes of the two directories a publication creates beside the final tree.
#: Both names are dot-prefixed by :func:`_staging_paths`, which is what makes
#: them unreachable over HTTP: ``app/utils/paths.resolve_artifact`` rejects any
#: request whose path carries a component beginning with a dot, so a partial
#: generation cannot be served even while it exists.  The process id keeps two
#: publications in two processes from building in one directory, and is what
#: :func:`_recover_interrupted_publication` reads to tell its own leavings from
#: another process's.  It is **not** a lock: two publications writing one
#: workspace at once would already be contending for the artifact itself -- and
#: for the three sibling artifacts, none of which is locked either -- so
#: serialising this one writer would buy a guarantee the artifact set as a
#: whole does not have.  AAP 0.4.1 has one producer per run, driven once from
#: ``app/services/report_service.py``.
_STAGING_INFIX: Final[str] = ".staging-"
_SUPERSEDED_INFIX: Final[str] = ".superseded-"


def _staging_paths(final: Path) -> tuple[Path, Path]:
    """Return the staging and renamed-aside siblings of ``final``.

    Siblings rather than children of a temporary directory, and that is a
    correctness requirement rather than a convenience: publication is a
    :func:`os.rename`, and a rename is only atomic -- indeed on most platforms
    only possible -- within one filesystem.  A sibling of the final directory
    is on the filesystem the final directory is on, whatever ``base`` a caller
    passed and wherever the build output happens to be mounted.

    This is the one path name this module derives rather than asking
    :mod:`app.utils.paths` for.  It is derived here because it is not an
    artifact location: it is a private staging area that exists only inside a
    single :func:`write_pretty_reports` call, its leading dot is exactly what
    that module's ``resolve_artifact`` refuses to serve, and the final
    directory it is a sibling of is still that module's to name.

    Args:
        final: The published tree's directory.

    Returns:
        ``(staging, superseded)``: the tree being built, and where the tree
        being replaced is moved while the swap happens.
    """
    pid = os.getpid()
    return (
        final.with_name(f".{final.name}{_STAGING_INFIX}{pid}"),
        final.with_name(f".{final.name}{_SUPERSEDED_INFIX}{pid}"),
    )


def _discard(directory: Path) -> None:
    """Remove ``directory`` and everything under it, if it is there.

    Used for the two publication scratch directories and for nothing else: no
    published artifact, no sibling artifact and never the build-output
    directory itself are
    passed here, which is what keeps "this writer replaces its own output
    directory" true and keeps ``--clean``'s job ``--clean``'s.

    Args:
        directory: A staging or renamed-aside directory.

    Raises:
        OSError: If the removal fails for a reason other than the directory
            not being there.  A failure here is real -- it would leave a
            dot-directory in the build output -- so it is not swallowed on
            the
            success path.  The failure path uses :func:`_discard_quietly`
            instead, because a fault is already being reported there and must
            not be replaced by a cleanup error.
    """
    if not directory.exists():
        return
    if directory.is_dir():
        shutil.rmtree(directory)
    else:
        # Pathological, but a file where the staging directory belongs would
        # otherwise fail every subsequent run with an unexplained mkdir error.
        directory.unlink()


def _discard_quietly(directory: Path) -> None:
    """Remove ``directory``, logging rather than raising if that fails.

    The failure path's form of :func:`_discard`.  A publication that is already
    failing has one job left -- leave the previous complete tree in place and
    report the fault that caused it -- and an exception raised while tidying up
    would replace that fault with a less useful one and skip the tidying that
    follows.  What remains behind is a dot-prefixed directory that no request
    can reach and that the next publication sweeps, so logging it is the
    proportionate response.

    Args:
        directory: A staging or renamed-aside directory.
    """
    try:
        _discard(directory)
    except OSError:
        logger.exception(
            "Could not remove the report publication directory %s; it is "
            "unreachable over HTTP and the next run removes it",
            directory,
        )


def _leftover_scratch(final: Path) -> list[Path]:
    """Return every publication scratch directory left beside ``final``.

    A run killed between the two renames of a publication leaves one behind.
    This function only *finds* them; what happens to each one is decided by
    :func:`_recover_interrupted_publication`, and the distinction is
    load-bearing: a renamed-aside tree may be the only complete generation
    there is, and a directory carrying another process's identifier may belong
    to a publication that is still running.  Neither is swept.

    Args:
        final: The published tree's directory, whose parent is scanned.

    Returns:
        The matching sibling paths, sorted, or an empty list when the parent
        directory does not exist yet or cannot be listed.
    """
    parent = final.parent
    prefixes = (
        f".{final.name}{_STAGING_INFIX}",
        f".{final.name}{_SUPERSEDED_INFIX}",
    )
    try:
        entries = sorted(parent.iterdir())
    except OSError:
        # No parent yet - a first run - or an unreadable one, which the
        # publication below will fail on with a far clearer error.
        return []
    return [entry for entry in entries if entry.name.startswith(prefixes)]


def _recover_interrupted_publication(final: Path, own: Sequence[Path]) -> None:
    """Put the tree back together after a publication that was killed part-way.

    The swap below is two renames, so there is one instant in which the
    published tree has been moved aside and its replacement has not yet taken
    its place.  A process killed in that instant leaves no published tree and
    one renamed-aside copy that **is the only complete generation in
    existence**.  This function is what makes that recoverable:

    * **an orphaned renamed-aside tree is restored, never removed.**  If
      ``final`` is absent and a superseded copy is there, it is renamed back
      first, before anything else in this call touches the filesystem -- so a
      publication that then fails on a render or a missing asset leaves that
      restored tree published, rather than leaving a reader with nothing.  The
      most recently modified copy wins, with the name breaking a tie, so the
      choice is deterministic;
    * **this call's own two scratch names are cleared**, because a previous
      run of *this* process cannot still be using them and
      :func:`ensure_dir` would otherwise build on top of a partial tree;
    * **scratch carrying another process's identifier is reported and left
      alone.**  Deleting it would be the one way this writer could destroy a
      concurrent publication's work, and no publisher can tell a dead
      process's leavings from a live one's portably -- a liveness probe is
      either unavailable or, on Windows, a request to terminate the process.
      What is left is dot-prefixed, so no request can reach it, and
      ``app/cli.py``'s ``--clean`` empties the build output directory on the
      next ordinary run.

    Args:
        final: The published tree's directory.
        own: The scratch paths this call will use, from :func:`_staging_paths`.
    """
    leftovers = _leftover_scratch(final)
    if not final.exists():
        superseded = [
            path
            for path in leftovers
            if f".{final.name}{_SUPERSEDED_INFIX}" in f".{path.name}" and path.is_dir()
        ]
        if superseded:
            candidate = max(
                superseded, key=lambda path: (path.stat().st_mtime, path.name)
            )
            try:
                os.rename(candidate, final)
            except OSError:
                logger.exception(
                    "Could not restore the report tree from %s to %s; the "
                    "previous generation is intact there and can be renamed "
                    "back by hand",
                    candidate,
                    final,
                )
            else:
                logger.warning(
                    "Restored %s from %s, left behind by an interrupted "
                    "report publication",
                    final,
                    candidate,
                )
                leftovers = _leftover_scratch(final)

    for leftover in leftovers:
        if leftover in own:
            logger.warning(
                "Removing %s, left behind by an interrupted report "
                "publication of this process",
                leftover,
            )
            _discard(leftover)
        else:
            logger.warning(
                "Leaving %s where it is: it carries another process's "
                "identifier, so a publication may still own it. It is "
                "unreachable over HTTP, and --clean removes it",
                leftover,
            )


def write_pretty_reports(
    result_set: ResultSet | None,
    base: Path | str | None = None,
    directory: Path | str | None = None,
    project_name: str = DEFAULT_PROJECT_NAME,
    build_date: str | None = None,
    environment: Environment | None = None,
) -> Path:
    """Write the whole report tree, pages and assets, and return its directory.

    **The tree is published as a whole or not at all.**  It is a directory
    artifact, so that guarantee cannot come from an atomic file write; it comes
    from building the whole tree in a staging sibling and swapping it into
    place:

    1. an earlier publication that was killed part-way is put back together
       (:func:`_recover_interrupted_publication`) and the staging tree is
       created;
    2. the assets are copied into staging and validated
       (:func:`copy_pretty_assets`);
    3. the pages are rendered and written one at a time, straight from
       :func:`iter_pretty_pages`, so one page string is alive at a time rather
       than the whole tree's worth of markup -- which matters on a failing run,
       where a scenario's base64 screenshot and traceback appear on its feature
       page, on the failures overview and on every tag page its tag reaches;
    4. the complete inventory in staging is verified: every required asset
       again, and every page the iterator produced;
    5. the published tree is renamed aside, staging is renamed into place, and
       the renamed-aside copy is deleted.  Two renames rather than one
       replace, because renaming a directory onto an existing directory fails
       on Windows and on POSIX alike and AAP 0.8 requires Windows support.

    **The bound on that last step, stated plainly.**  A directory cannot be
    exchanged for another in one indivisible operation with portable
    filesystem primitives: ``os.replace`` refuses a non-empty destination
    directory, a symbolic-link indirection would put a link where the
    reference tree has a directory and needs privileges on Windows, and
    deleting the published tree first would leave it absent for as long as the
    new one takes to write.  Two metadata renames are the narrowest window
    available, and what is visible inside it is the published tree *absent*,
    never partial -- a request for a page in that instant is the viewer's
    ordinary 404, and the next ordinary run of this writer restores the tree
    from the renamed-aside copy if a process died there
    (:func:`_recover_interrupted_publication`).  A reader therefore sees one
    complete generation or none, which is the guarantee the artifact contract
    needs; it never sees two mixed.

    Two consequences are the point of the exercise.  **Exactly one detail page
    per current feature and per current tag**: a page whose feature or tag has
    since disappeared was not written into the staging tree, so it is not in
    the published one either -- the old behaviour left it exposed, and a rerun
    that selected nothing at all kept every page of the run before it.  And
    **no mixed generation ever exists**: a fault at any point above leaves the
    previous complete tree exactly as it was, because nothing outside staging
    has been touched yet.

    What this does *not* do is delete anything that is not its own: no sibling
    artifact is read, moved or removed, and the build-output directory is
    never emptied.
    Replacing this writer's own directory is not the ``--clean`` step, which
    remains ``app/cli.py``'s and runs before the suite.

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
        project_name: The Project cell of the build-info table.  Empty by
            default, in which case the document's own ``project_name`` key
            supplies it; see :func:`render_pretty_pages`, which this function
            hands it to unchanged.
        build_date: The already-formatted Date cell; see
            :func:`format_build_date`, which is empty for a document carrying
            no timestamp.
        environment: An environment to render through; see
            :func:`build_environment`.

    Returns:
        The directory written.  With the default ``base`` it is
        :func:`app.utils.paths.pretty_reports_html_dir`, and the overview index
        inside it is exactly :func:`app.utils.paths.pretty_reports_index_path`
        -- the file served when a request names the artifact directory itself.

    Raises:
        jinja2.TemplateError: If a page cannot be rendered.
        FileNotFoundError: If a required asset is missing; see
            :func:`copy_pretty_assets`.
        OSError: If a directory cannot be created, a page cannot be written or
            the swap cannot be made.

        Every one of them propagates, because producing this artifact is the
        writer's contract with the run's exit table, whose writer-failure class
        names the failing writer on stderr.  None of them leaves a partial
        tree: the staging tree is removed, a tree already renamed aside is
        renamed back, and neither scratch directory survives the call.
    """
    final = Path(pretty_reports_html_dir(base) if directory is None else directory)
    staging, superseded = _staging_paths(final)

    _recover_interrupted_publication(final, (staging, superseded))

    moved_aside = False
    try:
        ensure_dir(staging)
        assets = copy_pretty_assets(staging)

        # Keyed by filename rather than appended to, so a detail-name collision
        # is one staged page here as it is one file on disk.
        staged: dict[str, None] = {}
        for filename, html in iter_pretty_pages(
            result_set,
            project_name=project_name,
            build_date=build_date,
            environment=environment,
        ):
            target = staging / filename
            # newline="\n" so a page written on Windows is byte-identical to
            # one written on Linux: the structure of this artifact must not
            # depend on which side of the pipeline's isUnix() branch produced
            # it.  A repeated filename overwrites, which is the detail-page
            # collision rule iter_pretty_pages documents.
            with open(target, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(html)
            staged[filename] = None
            logger.debug("Staged %s", target)

        # The inventory check, over staging and before the swap: the assets
        # once more, in case a page write disturbed one, and every page the
        # iterator produced.  Anything missing here means the tree would be
        # published incomplete, which is the one outcome this writer must not
        # produce.
        _require_assets(staging)
        missing_pages = [name for name in staged if not (staging / name).is_file()]
        if missing_pages:
            raise OSError(
                f"the staged report tree {staging} is missing "
                f"{len(missing_pages)} page(s) that were written into it: "
                + ", ".join(missing_pages)
            )

        # The swap.  Between the two renames the published tree is absent
        # rather than partial, which is the one instant this design cannot
        # remove; it is bounded by a rename of a directory that has already
        # been created on the same filesystem.
        if final.exists():
            os.rename(final, superseded)
            moved_aside = True
        os.rename(staging, final)
    except BaseException:
        # Restoration comes first and everything else is best-effort: the one
        # thing that must survive a failed publication is the tree that was
        # published before it.
        if moved_aside and not final.exists():
            try:
                os.rename(superseded, final)
            except OSError:
                logger.exception(
                    "Could not restore the previous report tree to %s; it is "
                    "intact at %s and can be renamed back by hand",
                    final,
                    superseded,
                )
        _discard_quietly(staging)
        if superseded.exists() and final.exists():
            # The published tree is in place - restored, or newly swapped in
            # before a later step failed - so the copy aside is scratch.  When
            # the restore could not be made, the copy aside is the only
            # surviving generation and is deliberately left where the log says
            # it is.
            _discard_quietly(superseded)
        raise
    else:
        # The swap has already happened, so the tree on disk is complete and
        # this writer has met its contract.  A cleanup fault from here is
        # therefore logged -- ``app/logging_config.py`` routes it to stderr and
        # the next publication sweeps what it left -- and deliberately not
        # raised: raising would have
        # ``app/services/report_service.py`` name this writer under the exit
        # contract's writer-failure class for an artifact that is in fact
        # published, which is a false diagnosis of a complete run.
        _discard_quietly(superseded)

    logger.info(
        "Published %d page(s) and %d asset(s) to %s",
        len(staged),
        len(assets),
        final,
    )
    return final
