"""HTML contract 2: the generated PrettyReports tree of report pages.

The port of the PrettyReports plugin the Java build ran
(``me.jvt.cucumber:reporting-plugin`` 7.2.0, pulling
``net.masterthought:cucumber-reporting`` 5.6.1 over Velocity), and a separate
contract from :mod:`app.reporting.html_report`, whose single page comes from
``io.cucumber:html-formatter`` 17.0.0 (AAP 0.3.4).

The tree published under :func:`app.utils.paths.pretty_reports_html_dir`: the
four overview pages (features, tags, steps, failures), one
``report-feature_<hash>.html`` per emitted feature, one
``report-tag_<hash>.html`` per tag, and the vendored asset set -- Bootstrap,
jQuery, Chart.js, tablesorter, Moment, the two icon fonts, the favicon and the
port's own ``css/main.css`` and ``js/report.js`` -- so that every page renders
offline from a CI workspace.

What this module deliberately does *not* own
--------------------------------------------
* **Markup.**  All six page templates and their four helpers already exist
  under ``app/templates/pretty/``.
* **Aggregation.**  :mod:`app.reporting.aggregation` is the normalised result
  model: every status, fold, count, duration, timestamp and selection rule is
  computed there and **read** here.  Its direct consumers are this module,
  ``app/reporting/html_report.py`` and ``app/reporting/rerun_report.py``, plus
  the templates of ``app/templates/pretty/``, which reach the authority's own
  functions through :data:`MODEL_GLOBALS` rather than holding folds of their
  own.  ``app/web/routes.py`` is not a consumer: the viewer tallies the parsed
  artifact itself, and a step-less element is ``unknown`` there against
  :data:`app.reporting.aggregation.EMPTY_ELEMENT_STATUS`, which is ``passed``,
  in every artifact this package writes.  That one difference is the boundary
  of the claim, and it is stated rather than assumed away.

  The names this module still exports for those calculations --
  :func:`status_token`, :func:`worst_status`, :func:`element_status`,
  :func:`element_duration_ns`, :func:`emitted_features`,
  :func:`build_tag_rows`, :func:`build_tag_totals` and the status vocabulary --
  are the authority's, re-exported or delegated to so that a consumer written
  against this module's surface keeps working while there is exactly one
  implementation underneath.
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

**Publication is a staged swap**, since "complete or untouched" cannot come
from one atomic write to a directory: assets and pages are built into a
dot-prefixed sibling -- a sibling so the swap is a rename within one
filesystem, dot-prefixed so ``resolve_artifact`` will not serve it -- the
inventory is verified, and then the tree is renamed aside, staging renamed
into place and the copy aside deleted.  A failure publishes a complete
generation or none at all, never a partial one: the previous tree is restored,
and where the restoring rename itself fails that tree is left intact at the
superseded scratch path the log names, to be renamed back by hand
(:func:`write_pretty_reports`).

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
feature carries ``status`` (the severity fold), ``verdict`` (the binary
PrettyReports one), ``duration_ns``, ``duration_samples`` and ``stats``, and
every element carries those plus ``steps_status`` (its steps-only fold) and
``effective_status``/``effective_verdict`` (its scenario unit's readings, the
preceding Background occurrence folded in).  Every step's ``result.status`` in
that copy is already the canonical token, dry-run mapping included.

**The templates read those values; they no longer derive any of them.**
``pretty/_element_tree.html`` held its own severity precedence, its own step
fold and its own duration sum, and ``feature.html`` and ``tag.html`` each
tallied their own statistics row, so a scenario whose after-hook failed and an
element carrying a status the model never produced both rendered as *passed*
beside statistics that counted them as failures, and the failures overview
listed only elements spelled ``failed`` while those same tables counted every
non-passed element.  The four templates that render the element tree now read
the decorated keys above, and where a mapping carries none -- a hand-built
context, or a fragment a page assembled -- they call the authority itself
through :data:`MODEL_GLOBALS`, the ``model_``-prefixed globals
:func:`build_environment` installs.  No template of the folder holds a
precedence tuple, a fold loop or a duration sum, which is what makes the rows
and totals above and the briefs beneath them two readings of one calculation
rather than two calculations.

Publication
-----------
The report tree :func:`app.utils.paths.pretty_reports_html_dir` names is a
**directory** artifact, so
"complete or untouched" cannot be had from one atomic file write.  It is had
from a staged swap instead, and every step of it runs relative to a directory
descriptor :func:`app.utils.paths.begin_directory_publication` verified and
holds open, so no step re-resolves a pathname that could have become a
symbolic link or a junction in between (CWE-59/CWE-367):

1. a dot-prefixed sibling of the final directory is the staging tree -- a
   sibling so that the swap is a rename on one filesystem, dot-prefixed so
   that :func:`app.utils.paths.resolve_artifact` rejects it and no partial
   generation is ever reachable over HTTP;
2. every page is rendered and budgeted *before* that tree is created, so a
   document over a budget costs the filesystem nothing; then the assets are
   copied into staging and validated, the pages are written into it, and the
   whole inventory -- every page-linked asset, all eleven fonts and every page
   the render produced -- is verified **before** anything is published;
3. the existing tree is renamed aside, staging is renamed into place, and the
   renamed-aside copy is deleted.  Two renames rather than one replace,
   because renaming a directory onto an existing directory fails on Windows
   and on POSIX alike, and AAP 0.8 requires Windows support;
4. on any failure the staging tree is removed and a tree already renamed
   aside is renamed back, so the previous complete generation survives; and no
   staging or renamed-aside directory outlives the call either way.  The
   removal is the publication's own
   :meth:`~app.utils.paths.ArtifactDirectoryPublication.discard_scratch`,
   which refuses a name that is not this tree's scratch and unlinks a link
   planted inside it rather than descending it -- a path-resolved cleanup here
   was what let a prepared directory *outside* the artifact root be deleted.

Every directory the publication creates is ``0700`` and every page and asset
it writes is ``0600`` (:data:`app.utils.paths.ARTIFACT_DIR_MODE` and
:data:`~app.utils.paths.ARTIFACT_FILE_MODE`), because a page carries failure
text and an embedded screenshot of the run: the tree is the owner's to read.
The asset copy takes the source's **bytes only** for the same reason -- a
``0644`` file vendored in a wheel must not carry its group and other bits into
the published tree (CWE-732/CWE-359).

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
* No path literal appears here, and **no path is derived here either**.
  Directories come from :func:`app.utils.paths.pretty_reports_html_dir`,
  :func:`~app.utils.paths.vendor_dir`, :func:`~app.utils.paths.static_dir` and
  :func:`~app.utils.paths.templates_dir`.  The emitted *page* and *asset*
  names below are the artifact contract itself -- the same four navigation
  targets ``pretty/_layout.html`` hard-codes -- and are declared once each,
  and each of them reaches the filesystem as a name *relative to the staging
  tree* rather than as a path this module joins.  The two scratch names the
  publication uses are the path authority's own
  (:data:`app.utils.paths.PUBLICATION_STAGING_INFIX` and
  :data:`~app.utils.paths.PUBLICATION_SUPERSEDED_INFIX`), which is what lets
  that module bind them to a verified parent descriptor and refuse a scratch
  name it does not recognise; this module used to spell them itself, and a
  writer that spells a path is a writer that resolves one.

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

The one input this writer refuses outright is a document whose **size**, rather
than whose content, is beyond what a report can be: the page set fans out per
feature and per distinct tag, so a document that is small and schema-valid can
still ask for a tree orders of magnitude larger than itself.
:data:`MAX_PRETTY_TAGS`, :data:`MAX_PRETTY_DETAIL_PAGES` and
:data:`MAX_PRETTY_OUTPUT_BYTES` bound that, and exceeding one raises
:exc:`PrettyReportBudgetError` -- the same writer-failure exit class, refusing
rather than publishing a truncated report.  All three are applied **before the
writer touches the filesystem at all**: the counts from the model, the bytes
as the pages are produced in memory, and the staging tree is not created until
every one of them has passed.  A refused document therefore costs no directory,
no asset copy and no page write, which is what makes the budgets a bound on
this writer's work rather than only on what it publishes.  No legitimate run
comes near a budget; see their own declarations for the measured numbers.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, FileSystemLoader

# Names imported here are re-exported deliberately: a consumer that imports
# ``status_token`` or ``element_status`` from this module reaches the one
# implementation of each rather than a second one.
from app.reporting.aggregation import (
    KNOWN_STATUSES,
    STATUS_PRECEDENCE,
    STATUS_READING_ORDER,
    UNKNOWN_STATUS,
    as_mapping,
    as_text,
    build_row_totals,
    element_duration_ns,
    element_status,
    element_steps_status,
    element_verdict,
    feature_status,
    feature_verdict,
    is_passed_token,
    is_scenario_element,
    is_selected,
    mappings,
    normalize_run,
    parse_timestamp,
    selected_features,
    stats_row,
    status_token,
    tag_row,
    unit_status,
    unit_verdict,
    worst_status,
)
from app.reporting.aggregation import (
    build_tag_rows as _aggregate_tag_rows,
)
from app.reporting.events import JsonDict, ResultSet
from app.utils.paths import (
    PRETTY_OVERVIEW_INDEX,
    ArtifactDirectoryPublication,
    begin_directory_publication,
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
    "MAX_PRETTY_DETAIL_PAGES",
    "MAX_PRETTY_OUTPUT_BYTES",
    "MAX_PRETTY_TAGS",
    "MODEL_GLOBALS",
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
    "PrettyReportBudgetError",
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

logger = logging.getLogger(__name__)

#: ``java.lang.Integer.MAX_VALUE``.  ``Util.toValidFileName`` adds it to the
#: hash "to eliminate minus character which might be returned by hashCode()",
#: which its own arithmetic achieves for every hash except
#: ``Integer.MIN_VALUE``: that one lands on ``-1`` and keeps the minus sign.
#: :func:`to_valid_file_name` reproduces the offset and
#: :data:`_DETAIL_PAGE_PATTERN` admits the resulting filename.
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

# Emitted page names.  These are the artifact contract, not filesystem paths:
# they are the four navigation targets ``pretty/_layout.html`` hard-codes and
# the two detail-page shapes the reference tree carries, so they are declared
# once here and reach the templates as passed-in values.
PAGE_SUFFIX: Final[str] = ".html"

#: The overview index, and the page served when a request names the artifact
#: directory itself.  Taken from :mod:`app.utils.paths` so the name this module
#: writes and the name that module resolves have a single owner.
OVERVIEW_FEATURES_PAGE: Final[str] = PRETTY_OVERVIEW_INDEX

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

FEATURE_TEMPLATE: Final[str] = "pretty/feature.html"
TAG_TEMPLATE: Final[str] = "pretty/tag.html"

# The vendored files are the reference generator's own output and part of the
# report contract, not a design system: nothing here upgrades, swaps, restyles
# or CDN-links them.

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

    The JVM's definition is ``s[0]*31^(n-1) + ... + s[n-1]``, evaluated in
    32-bit two's-complement arithmetic that overflows silently.  The overflow
    is *load-bearing* -- it is why the generator's filenames are the numbers
    they are -- so the accumulator is masked to 32 bits on every iteration and
    then reinterpreted as signed.

    Args:
        text: The string to hash.  An empty string hashes to ``0``, as in
            Java.

    Returns:
        The hash in ``[-2147483648, 2147483647]``.

    Raises:
        TypeError: If ``text`` is not a :class:`str`.  A programming error at
            a call site rather than report data, and a coerced value would
            produce a filename no test could have predicted.

    Examples:
        >>> java_hash_code("@Smoke")
        1912275215
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

    5.6.1 computes
    ``Long.toString((long) fileName.hashCode() + Integer.MAX_VALUE)``, its own
    comment explaining that ``MAX_VALUE`` is added "to eliminate minus
    character which might be returned by hashCode()".  The cast to ``long``
    before the addition is the detail that matters: the sum is evaluated in 64
    bits, so it must not be masked to 32.

    Args:
        text: The value to hash -- a full ``file:``-prefixed feature URI for a
            feature page, or a tag name including its leading ``@`` for a tag
            page.

    Returns:
        The offset hash as a decimal string, somewhere in ``[-1, 4294967294]``,
        ready to be spliced into a filename.  :func:`local_page_href` admits
        exactly that range, so every name built from this value is a
        destination the pages may link -- ``-1`` included.

    Raises:
        TypeError: If ``text`` is not a :class:`str`; see
            :func:`java_hash_code`.

    Examples:
        >>> to_valid_file_name("@Smoke")  # report-tag_4059758862.html
        '4059758862'
    """
    return str(java_hash_code(text) + INT32_MAX)


def feature_page_name(uri: str) -> str:
    """Return the detail-page filename for a feature URI.

    Reproduces ``Feature.calculateReportFileName(jsonFileNo)``, which is
    ``"report-feature_" + (jsonFileNo > 0 ? jsonFileNo + "_" : "") +
    toValidFileName(uri) + ".html"``.  The numeration segment is never
    emitted: ``jsonFileNo`` exists only to disambiguate one feature appearing
    in two input JSON files, and every worker's results are merged into one
    document before a page is rendered, so it is always ``0``.

    The hashed input is the **full ``file:``-prefixed URI**, not the bare path
    and not the feature name.  Two consequences, both intended: the names
    differ from the committed ones because the features directory moved (AAP
    deviation 18), and the two pairs of features in this suite that share an
    id still get two distinct pages.

    Args:
        uri: The feature's ``uri`` from the result model, e.g.
            ``"file:features/Crm.feature"``.

    Returns:
        The page filename, e.g. ``"report-feature_1364259633.html"``.

    Raises:
        TypeError: If ``uri`` is not a :class:`str`; see :func:`java_hash_code`.
    """
    return f"{FEATURE_PAGE_PREFIX}{to_valid_file_name(uri)}{PAGE_SUFFIX}"


def tag_page_name(tag_name: str) -> str:
    """Return the detail-page filename for a tag.

    Reproduces ``Tag.generateFileName``, which is
    ``String.format("report-tag_%s.html", toValidFileName(tagName))``.  The
    hashed input **includes the leading ``@``**, part of the tag name in this
    port's result model as it was in the JVM's.  Tag text does not move with
    the feature directory, so tag-page filenames are unchanged from the
    committed ones: ``@Smoke`` is ``report-tag_4059758862.html`` here and in
    the reference tree.

    Args:
        tag_name: The tag, e.g. ``"@Smoke"``.

    Returns:
        The page filename, e.g. ``"report-tag_4059758862.html"``.

    Raises:
        TypeError: If ``tag_name`` is not a :class:`str`; see
            :func:`java_hash_code`.
    """
    return f"{TAG_PAGE_PREFIX}{to_valid_file_name(tag_name)}{PAGE_SUFFIX}"


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

#: The inclusive bounds of the hash segment :func:`to_valid_file_name`
#: produces, and therefore of the segment the allowlist below admits.  Both are
#: the offset hash's own range rather than numbers chosen here: the generator
#: adds :data:`INT32_MAX` to a signed 32-bit hash in 64-bit arithmetic, so the
#: lowest value is ``Integer.MIN_VALUE + INT32_MAX``, which is ``-1``, and the
#: highest is ``Integer.MAX_VALUE + INT32_MAX``, which is ``2 * INT32_MAX``.
_MIN_PAGE_HASH: Final[int] = -1
_MAX_PAGE_HASH: Final[int] = 2 * INT32_MAX

#: Name of the capturing group holding a detail page's hash segment, so the
#: parse below names it rather than counting groups.
_PAGE_HASH_GROUP: Final[str] = "page_hash"

#: The hash segment, as the two branches the generator can actually write: the
#: single negative value ``-1``, or a non-negative decimal in the canonical
#: spelling :func:`str` produces -- no sign, and no leading zero except the
#: value zero itself.  ``[0-9]`` rather than ``\d``, which in Python also
#: matches the decimal digits of other scripts -- a filename this writer cannot
#: produce.  The digit run is bounded by the width of :data:`_MAX_PAGE_HASH`
#: instead of being open-ended, which keeps the integer parse in
#: :func:`local_page_href` bounded work on a value that arrives from a result
#: document; the range check there is what rejects the in-width values above
#: the maximum, such as ``4294967295``.
_PAGE_HASH_EXPRESSION: Final[str] = (
    f"{re.escape(str(_MIN_PAGE_HASH))}|0|[1-9][0-9]{{0,{len(str(_MAX_PAGE_HASH)) - 1}}}"
)

#: The two detail-page shapes: a fixed prefix, the hash segment above and the
#: suffix.  Assembled from :data:`FEATURE_PAGE_PREFIX`,
#: :data:`TAG_PAGE_PREFIX` and :data:`PAGE_SUFFIX` for the same reason the
#: fixed names are, and anchored at both ends by the ``fullmatch`` in
#: :func:`local_page_href`.
_DETAIL_PAGE_PATTERN: Final[re.Pattern[str]] = re.compile(
    "(?:{feature}|{tag})(?P<{group}>{page_hash}){suffix}".format(
        feature=re.escape(FEATURE_PAGE_PREFIX),
        tag=re.escape(TAG_PAGE_PREFIX),
        group=_PAGE_HASH_GROUP,
        page_hash=_PAGE_HASH_EXPRESSION,
        suffix=re.escape(PAGE_SUFFIX),
    )
)


def local_page_href(candidate: Any) -> str:
    """Return ``candidate`` when it names a page of this tree, else ``""``.

    The single validation point for every dynamic ``href`` the six page
    templates emit, registered as a Jinja global by :func:`build_environment`
    -- the only environment that renders ``pretty/*`` -- so every link sink in
    the folder reaches one implementation.  Accepted: the four fixed overview
    filenames and a name matching :data:`_DETAIL_PAGE_PATTERN`.  Everything
    else is rejected and the caller renders its label as plain text rather
    than as a link to nowhere: any scheme (``javascript:`` and ``data:``
    included, which autoescaping does not neutralise -- it escapes HTML, and
    they carry no character HTML escaping touches), any absolute,
    protocol-relative, parent-relative or nested path, any query or fragment,
    whitespace, a control character, a non-string and an empty value.

    **What is accepted** is exactly what this module writes, stated as the two
    shapes rather than as a resemblance to them:

    * the four fixed overview filenames --
      :data:`OVERVIEW_FEATURES_PAGE`, :data:`OVERVIEW_TAGS_PAGE`,
      :data:`OVERVIEW_STEPS_PAGE` and :data:`OVERVIEW_FAILURES_PAGE`; and
    * :data:`FEATURE_PAGE_PREFIX` or :data:`TAG_PAGE_PREFIX`, then a hash
      segment, then :data:`PAGE_SUFFIX`, where the segment is either the single
      negative value ``-1`` or a non-negative decimal no greater than
      ``2 * INT32_MAX`` written without a leading zero.  That is the whole
      range :func:`to_valid_file_name` can return -- ``-1`` is the offset
      value of ``Integer.MIN_VALUE`` and the generator writes it as readily as
      any other -- so a page this writer emits is a page this function admits,
      and no page it admits is one this writer could not have emitted.

    Everything else is rejected, and the caller renders its label as plain
    text -- never as a link to nowhere and never as an empty anchor.  Rejected
    outright, therefore:

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
            :class:`jinja2.Undefined` is answered with ``""``, not raised on.

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
        >>> local_page_href("report-feature_-1.html")  # the Integer.MIN_VALUE hash
        'report-feature_-1.html'
        >>> local_page_href("report-tag_-1.html")
        'report-tag_-1.html'
        >>> local_page_href("report-tag_-2.html")      # below the hash's range
        ''
        >>> local_page_href("report-tag_4294967295.html")  # above it
        ''
        >>> local_page_href("report-tag_-0.html")
        ''
        >>> local_page_href("report-tag_+1.html")
        ''
        >>> local_page_href("report-tag_01.html")
        ''
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
    detail = _DETAIL_PAGE_PATTERN.fullmatch(candidate)
    if detail is not None:
        # The shape is right; the value still has to be one the offset hash can
        # take.  The pattern bounds the digit run to the width of the maximum,
        # so this parse is bounded work and cannot raise on a value that came
        # out of a result document, and the comparison then rejects the
        # in-width impossibilities -- 4294967295 being the one a mask-based
        # implementation of the hash would have produced.
        page_hash = int(detail.group(_PAGE_HASH_GROUP))
        if _MIN_PAGE_HASH <= page_hash <= _MAX_PAGE_HASH:
            return candidate
    logger.warning(
        "Rejected %r as a page destination: it is not a page this writer emits",
        candidate,
    )
    return ""


def emitted_features(result_set: ResultSet | None) -> list[JsonDict]:
    """Return the features that get a row and a detail page, in source order.

    Delegates to :func:`app.reporting.aggregation.selected_features`, which is
    the one place the selection rule lives: drop a Background-occurrence-plus-
    scenario unit whose members carry ``"selected": False``, then drop a
    feature left with no test case at all.  It is the rule
    ``app/reporting/cucumber_json.py`` applies, so the JSON artifact, both
    HTML artifacts and the viewer describe the same run; under the default
    ``@Smoke`` filter it reduces this suite's ten features to the one the
    reference artifact carries.

    Nothing is sorted and nothing is mutated: the model's order is source
    order and it is preserved, and a feature that loses a unit is copied
    rather than edited.

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

    The tag set of the run, and the order the tag pages are emitted in.  Three
    rules, each measured against the reference:

    * **feature-level tags propagate onto every scenario**, as
      ``pretty/tag.html`` matches them: ``@Smoke`` is declared once at the top
      of ``Crm.feature`` and belongs to each of its scenarios;
    * **a scenario with no tags of its own and no feature tag contributes
      nothing**; an untagged scenario omits the ``tags`` key, so an empty tag
      set is ordinary rather than an error;
    * **an unselected scenario is not a tag's scenario**: it never ran, so a
      tag carried only by unselected scenarios gets no row and no page.

    Backgrounds are never subjects here; a tag page still renders the
    Background occurrences of the scenarios it lists, by that template's doing.

    Args:
        features: The features being emitted, from :func:`emitted_features`.

    Returns:
        An insertion-ordered mapping from tag name to the scenario elements
        carrying it, in first-appearance order, so a rerun over one input
        cannot reshuffle the emitted page set.
    """
    grouped: dict[str, list[JsonDict]] = {}
    # Identity of the subjects already recorded for each tag, so the dedup
    # below is a hash lookup rather than a scan of a list that grows with every
    # scenario the tag carries.  ``id()`` is safe as the key because
    # ``features`` is alive for as long as the returned mapping is: every
    # subject is an element of a feature the caller still holds, so no identity
    # can be recycled while this set is in use.  The ordered list is what the
    # caller reads.
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


def _tag_row(name: str, subjects: Sequence[JsonDict], href: str) -> JsonDict:
    """Build one tags-overview row.

    A delegation to :func:`app.reporting.aggregation.tag_row`, kept because
    this module's own callers and its documentation are written in terms of a
    tag's *subjects*.  The arithmetic is the authority's -- the
    ``net.masterthought:cucumber-reporting`` 5.6.1 tally rules, the same ones
    it applies to a feature row, so a tag row and a feature row grade one
    scenario alike.

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
    same arithmetic over different rows, so the two pages cannot report totals
    that disagree.

    Args:
        rows: The rows from :func:`build_tag_rows`.

    Returns:
        A mapping carrying the nine counts, ``duration_ns``, ``features`` and
        ``features_passed``.  Every value is ``0`` for an empty row set, so the
        footer still renders.
    """
    return build_row_totals(rows)


def format_build_date(
    result_set: ResultSet | None = None,
    now: datetime | None = None,
) -> str:
    """Return the already-formatted Date cell of every page's build-info table.

    ``pretty/_layout.html`` documents ``build_date_display`` as arriving
    formatted, because locale, timezone and format decisions belong here.  The
    shape is the reference's own -- ``07 Sep 2022, 15:39`` -- assembled from
    :data:`MONTH_ABBREVIATIONS` rather than ``strftime('%b')``, since ``%b``
    follows the process locale and this suite deliberately drives one scenario
    under a French locale.  Moments are rendered in UTC.

    The moment comes from the document -- ``generated_at``, then ``started_at``
    -- so that two renders of one document agree.  A document carrying neither
    answers the empty string, which ``pretty/_layout.html`` renders as an empty
    cell: there is no clock fallback, because the render-time clock is when
    somebody rendered the report and not when the run happened.

    Args:
        result_set: The merged result document, or ``None``.
        now: An explicit moment to use when the document carries no usable
            timestamp.  Defaults to ``None``, which yields ``""``.

    Returns:
        The formatted date, e.g. ``"07 Sep 2022, 13:39"``; ``""`` when neither
        the document nor ``now`` supplies a moment.  A timestamp present but
        unparseable is returned verbatim.
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
# Fan-out budgets
#
# The page set this writer produces is a function of the result document, and
# the document's own schema bounds only one level at a time.
# ``app/reporting/events.py`` permits MAX_FEATURES = 1000 features,
# MAX_ELEMENTS_PER_FEATURE = 10_000 elements in each, MAX_TAGS_PER_LEVEL = 100
# tags on each feature and on each element, MAX_EMBEDDINGS_PER_HOOK = 100
# attachments on each hook at MAX_EMBEDDING_DATA_LENGTH = 16 MiB apiece, and
# MAX_DOCUMENT_NODES = 250_000 nodes overall.  Those limits are compatible with
# a document carrying on the order of 100,000 *distinct* tags, and this writer
# emits one detail page per distinct tag, each page repeating the tag chips,
# failures and screenshots of every scenario that carries the tag.  So a
# document that is small and entirely schema-valid can ask for an output tree
# orders of magnitude larger than itself: measured here, a single scenario
# carrying 1,000 distinct tags renders 1,005 pages and 87 MB of HTML, and the
# node budget allows far more than that.  That is amplification (CWE-400), and
# a per-level input limit cannot express it, so the aggregate limits are this
# writer's own and live here.
#
# The numbers are deliberately generous, of the same order as the event
# schema's own caps, so that no legitimate run can meet one: this suite
# declares 18 distinct tags across 10 feature files, so a run with no tag
# filter at all publishes four overview pages, ten feature pages and eighteen
# tag pages, well under a megabyte between them.  What the budgets refuse is a
# document nobody could read the report of.
#
# Refusing is the correct answer rather than truncating, because a truncated
# report is a report that quietly omits results: AAP 0.4.1's exit contract has
# a writer-failure row -- non-zero exit, the artifacts written before the
# failure left in place, the failing writer named on stderr -- and
# ``app/services/report_service.py`` turns any exception from a writer into
# exactly that outcome.  A refused tree therefore fails the run loudly with
# the previous complete generation still published.
# --------------------------------------------------------------------------- #

#: The largest number of distinct tags this writer will give pages to.  Nearly
#: two orders of magnitude above the 18 tags this suite declares, and of the
#: same order as the event schema's own MAX_FEATURES = 1000, so a run that
#: meets it is not a run whose report anybody navigates tag by tag.
MAX_PRETTY_TAGS: Final[int] = 1000

#: The largest number of detail pages -- feature pages plus tag pages -- one
#: published tree may hold.  It is the largest legitimate page set there is:
#: every feature the result schema admits (MAX_FEATURES = 1000) plus every tag
#: this writer admits (:data:`MAX_PRETTY_TAGS`), which is what keeps a
#: document that stays under the tag cap from reaching the same amplification
#: through features instead.
MAX_PRETTY_DETAIL_PAGES: Final[int] = 2000

#: The cumulative budget for the page bytes of one tree, accumulated by
#: :func:`iter_pretty_pages` as the pages are produced and therefore applied
#: before any of them is written.  The count caps above bound how many pages
#: there are and this bounds how large they may be together, which is the half
#: of the amplification a page count cannot see: one 16 MiB screenshot -- the
#: event schema's MAX_EMBEDDING_DATA_LENGTH -- is repeated on its feature page,
#: on the failures overview and on every tag page its scenario's tags reach.
#:
#: 256 MiB, the same order as the event schema's own MAX_RESULT_FILE_BYTES for
#: one worker's input, so the output of a run is bounded by roughly what its
#: input may be rather than by a multiple of it.  It bounds memory as well as
#: disk, because :func:`write_pretty_reports` holds the page set while it
#: writes; a real run of this suite produces a tree three orders of magnitude
#: inside it, so the budget is a refusal threshold rather than a target.  The
#: 22-file vendored asset set is a fixed cost and is not counted against it.
MAX_PRETTY_OUTPUT_BYTES: Final[int] = 256 * 1024 * 1024


class PrettyReportBudgetError(ValueError):
    """A result document asks for more report than this writer will produce.

    Raised by :func:`iter_pretty_pages` and :func:`write_pretty_reports` when a
    document exceeds :data:`MAX_PRETTY_TAGS`, :data:`MAX_PRETTY_DETAIL_PAGES`
    or :data:`MAX_PRETTY_OUTPUT_BYTES`.  It derives from :class:`ValueError`
    because the fault is in the input rather than in the filesystem: nothing
    has gone wrong with the writing, and a diagnosis of ``OSError`` would send
    a reader of the build log looking at a disk.  The message names the
    measured amount and the budget it passed, so the log says which document
    was refused and by how much.

    Like every other fault of this writer it reaches
    ``app/services/report_service.py`` as the exit contract's writer-failure
    class, and it is raised before anything is published, so the previously
    published tree -- if there is one -- stands untouched.
    """


def _require_counts_within_budget(tag_count: int, detail_pages: int) -> None:
    """Refuse a fan-out whose page counts exceed the aggregate budgets.

    Both counts are known from the model alone, before a template is rendered
    or a directory is created, which is why they are checked together and
    first: a document over either budget is refused without this writer having
    touched the filesystem at all.

    Args:
        tag_count: The number of distinct tags, from :func:`collect_tags`.
        detail_pages: The number of distinct detail-page filenames the fan-out
            would produce -- feature pages plus tag pages, collisions counted
            once, since a collision is one file.

    Raises:
        PrettyReportBudgetError: If ``tag_count`` exceeds
            :data:`MAX_PRETTY_TAGS` or ``detail_pages`` exceeds
            :data:`MAX_PRETTY_DETAIL_PAGES`.
    """
    if tag_count > MAX_PRETTY_TAGS:
        raise PrettyReportBudgetError(
            f"the result document carries {tag_count} distinct tags, more than "
            f"the {MAX_PRETTY_TAGS} this writer gives detail pages to "
            "(MAX_PRETTY_TAGS); no report was written"
        )
    if detail_pages > MAX_PRETTY_DETAIL_PAGES:
        raise PrettyReportBudgetError(
            f"the result document asks for {detail_pages} detail pages, more "
            f"than the {MAX_PRETTY_DETAIL_PAGES} one report tree may hold "
            "(MAX_PRETTY_DETAIL_PAGES); no report was written"
        )


def _require_fan_out_within_budget(result_set: ResultSet | None) -> None:
    """Check the count budgets for ``result_set`` before anything is created.

    The form :func:`write_pretty_reports` calls, before it derives a staging
    path or creates a directory.  It walks the model a second time -- the
    render walks it too -- and that is the cheaper half of the exchange by a
    wide margin: the walk is bounded by the document's own node budget, while
    the alternative is discovering the refusal after a staging tree has been
    created and the whole vendored asset set copied into it.

    Args:
        result_set: The merged result document, or ``None``.

    Raises:
        PrettyReportBudgetError: If the document is over either count budget;
            see :func:`_require_counts_within_budget`.
    """
    features = emitted_features(result_set)
    tags = collect_tags(features)
    # The page names rather than the subjects, because two subjects hashing to
    # one name are one page on disk -- the collision rule iter_pretty_pages
    # documents -- and this budget is about pages.  feature_page_name directly
    # rather than feature_href_map, so the URI-less-feature warning is not
    # logged twice for one call.
    pages = {
        feature_page_name(uri)
        for uri in (as_text(feature.get("uri")) for feature in features)
        if uri
    }
    pages.update(tag_page_name(name) for name in tags)
    _require_counts_within_budget(len(tags), len(pages))


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

#: The aggregation authority, reachable from inside a template.
#:
#: Every template of ``app/templates/pretty/`` reads the decorated value
#: :func:`app.reporting.aggregation.decorate_feature` put on the mapping it was
#: handed and, where a mapping carries none -- a hand-built context, or a
#: fragment assembled by a page -- asks the function below that answers the
#: same question.  No template of the folder holds a precedence tuple, a fold
#: loop or a duration sum, and that is the point of installing these: a
#: template-side fold reads steps alone, so it grades a hook-only failure and a
#: status the model never produced as *passed* beside statistics that count
#: both as failures, and it tests a literal ``failed`` token where
#: ``net.masterthought:cucumber-reporting:5.6.1`` selects every element whose
#: ``Status.isPassed()`` is false.
#:
#: **The naming rule, which is the render contract half of this:** every entry
#: is the authority's own callable or constant under a ``model_``-prefixed
#: name.  The prefix is what keeps these apart from the macro of the same
#: question a page imports as ``tree.element_status`` -- the macro is the
#: reader, the global is what it delegates to -- so a template can never
#: shadow one with the other.  They are installed as **globals** rather than
#: filters for the reason :func:`local_page_href` is: a global is reachable
#: from a macro imported with the plain ``{% import %}`` form, which is how
#: every helper in that folder is imported, whereas a context-dependent lookup
#: would not be.
MODEL_GLOBALS: Final[Mapping[str, Any]] = {
    # The severity fold of one element -- its steps *and* its hooks, so a
    # scenario whose after-hook failed is not badged as a pass.
    "model_element_status": element_status,
    # The steps-only fold, which is what the generator's own "Steps" group
    # brief answers for and the only reading that group takes.
    "model_element_steps_status": element_steps_status,
    # The binary PrettyReports readings, for the not-passed selection rule
    # ``Status.isPassed()`` gives 5.6.1's failures overview.
    "model_element_verdict": element_verdict,
    "model_feature_verdict": feature_verdict,
    # The feature fold, over every element including Background occurrences.
    "model_feature_status": feature_status,
    # The scenario-unit readings: a Background occurrence folded with the
    # scenario it precedes, for counting and selection.
    "model_unit_status": unit_status,
    "model_unit_verdict": unit_verdict,
    # Step durations in nanoseconds, hooks excluded.
    "model_element_duration_ns": element_duration_ns,
    # The single canonicalisation point and the single pass predicate.
    "model_status_token": status_token,
    "model_is_passed_token": is_passed_token,
    # The severity fold itself, for the one container whose status is the fold
    # of the rows the template just rendered rather than a reading of an
    # element: ``pretty/_element_tree.html``'s hook sections.  It is the
    # authority's own ``roll_up_status``, so a hook group's badge cannot rank
    # two statuses differently from the element brief above it.
    "model_worst_status": worst_status,
    # One statistics row -- the same arithmetic the overview rows carry, so a
    # detail page that has to build its own row cannot state a different
    # number from the overview that links it.
    "model_stats_row": stats_row,
    # The two declared orders, as data.
    "model_status_precedence": STATUS_PRECEDENCE,
    "model_status_reading_order": STATUS_READING_ORDER,
}


def build_environment() -> Environment:
    """Build the template environment this writer renders through.

    A plain :class:`jinja2.Environment` rather than Flask's, because this
    writer runs inside a worker process that never builds an application, and
    the pages are opened over the file protocol straight from a CI workspace
    where a framework-built static URL could not resolve.  The loader root is
    :func:`app.utils.paths.templates_dir`, which is what makes the templates'
    own ``pretty/_layout.html`` and ``partials/status_badge.html`` references
    resolve, and autoescaping is on for every template regardless of
    extension: feature names carry quotation marks and apostrophes, and the
    reference generator escapes its own output too.

    :func:`local_page_href` is installed as a **global**, not a filter, and
    installing it here is what puts one link allowlist behind every sink in
    the folder: a global is reachable from a macro imported with the plain
    ``{% import %}`` form, which passes no context, and every helper in
    ``app/templates/pretty/`` is imported that way on purpose.

    :data:`MODEL_GLOBALS` is installed the same way and for the same reason:
    the aggregation authority's own functions, under ``model_``-prefixed
    names, so a template answers a status, verdict or duration question by
    asking the one implementation of it instead of holding a second.  See that
    constant for the whole set and the naming rule.

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
    environment.globals.update(MODEL_GLOBALS)
    return environment


def _element_owner_map(features: Sequence[JsonDict]) -> dict[int, JsonDict]:
    """Map each element's identity to the feature it belongs to.

    Built **once** for a whole render and handed to every tag page, so the
    walk over every element of every feature happens once rather than once per
    tag.  Identity rather than a key, because two features in this suite may
    share an id and a name -- Contact with Inventory and Login with Notes -- so
    a name lookup would attribute a scenario to the wrong feature's page.

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

    ``pretty/tag.html`` accepts either the whole feature list, which it groups
    feature by feature, or a flat element list.  The reference tag page is the
    flat shape: its ``report-tag_4059758862.html`` lists four scenario
    elements in one container, each opened with a ``Feature: <name>`` link
    row.  The flat form is also the honest one here, the subjects having been
    selected by :func:`collect_tags` already.

    ``pretty/_element_tree.html``'s ``elements_block`` reads ``feature_href``
    and ``feature_name`` off each item, so each element is copied shallowly
    and the two keys added -- a copy because the model belongs to the caller
    and the other pages render the same objects.

    Args:
        subjects: The tag's selected scenario elements, from
            :func:`collect_tags` over the decorated features.
        owner: The element-to-feature map from :func:`_element_owner_map`.
        feature_links: The map from :func:`feature_href_map`.

    Returns:
        One shallow copy per subject, in document order, each carrying
        ``feature_href`` and ``feature_name``.  A subject whose feature has no
        page link keeps the name, which the template renders as plain text.
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
    """Render the tree one page at a time, within every fan-out budget.

    The budget-enforcing form, and the only one anything outside this module
    uses: it is :func:`_iter_rendered_pages` with the cumulative
    :data:`MAX_PRETTY_OUTPUT_BYTES` budget applied, so **every** consumer --
    :func:`render_pretty_pages`, :func:`write_pretty_reports` and a caller
    iterating this function directly -- is bounded by one implementation
    rather than by its own accounting.

    The two count budgets are checked by the inner generator before a page is
    rendered.  This one is cumulative and so can only be measured page by
    page: each page's encoded length is added as it is produced and the total
    is checked **before the page is yielded**, so a consumer never receives,
    writes or copies a byte past the budget.  The page that crosses it has been
    rendered -- a page's size is not knowable without rendering it -- and is
    then dropped rather than handed on.

    Args:
        result_set: The merged result document, or ``None`` for a run that
            produced nothing.
        project_name: The Project cell of the build-info table.
        build_date: The already-formatted Date cell.
        environment: An environment to render through.

    Yields:
        ``(output filename, HTML)`` pairs, in the order
        :func:`_iter_rendered_pages` documents.  Nothing is written and no
        directory is touched.

    Raises:
        PrettyReportBudgetError: If the document is over either count budget,
            raised on the first iteration before any page is rendered; or if
            the pages produced so far exceed
            :data:`MAX_PRETTY_OUTPUT_BYTES`, raised instead of yielding the
            page that crossed it.
        jinja2.TemplateError: If a template is missing, malformed or fails to
            render.  Deliberately not swallowed -- it is a genuine render
            fault, not a test outcome.
    """
    produced_bytes = 0
    for filename, html in _iter_rendered_pages(
        result_set,
        project_name=project_name,
        build_date=build_date,
        environment=environment,
    ):
        # The encoded length, not the character count: the budget is about the
        # bytes a publication writes, and the temporary is released
        # immediately rather than held beside the page string it measures.
        produced_bytes += len(html.encode("utf-8"))
        if produced_bytes > MAX_PRETTY_OUTPUT_BYTES:
            raise PrettyReportBudgetError(
                "the report tree for this result document reached "
                f"{produced_bytes} page byte(s) at {filename}, more than the "
                f"{MAX_PRETTY_OUTPUT_BYTES} one tree may hold "
                "(MAX_PRETTY_OUTPUT_BYTES); no report was written"
            )
        yield filename, html


def _iter_rendered_pages(
    result_set: ResultSet | None,
    project_name: str = DEFAULT_PROJECT_NAME,
    build_date: str | None = None,
    environment: Environment | None = None,
) -> Iterator[tuple[str, str]]:
    """Render the tree one page at a time, in the order the pages are written.

    The streaming form of :func:`render_pretty_pages` and the one
    :func:`write_pretty_reports` uses: the shared maps, rows and statistics are
    built once, then each page is rendered, yielded and dropped before the next
    is rendered, so only one page's markup is alive at a time.

    The page order is the artifact contract's own: the four overviews -- always
    yielded, even for an empty document, since the exit contract requires all
    four artifacts -- then the feature pages in source order, then the tag
    pages in first-appearance order.  A filename two feature URIs hash to is
    yielded twice, with a warning, so the later page wins.

    **The size of the fan-out is bounded.**  The distinct-tag and detail-page
    budgets are checked here, once, after the model has been read and before
    the first page is rendered, so a document that asks for more report than
    :data:`MAX_PRETTY_TAGS` or :data:`MAX_PRETTY_DETAIL_PAGES` allows yields
    nothing at all rather than a partial tree.  The cumulative
    :data:`MAX_PRETTY_OUTPUT_BYTES` budget is the one this generator does not
    apply: it is applied by :func:`iter_pretty_pages`, which wraps this one
    and is what every consumer calls, so no caller can reach an unbounded
    stream of pages.

    Args:
        result_set: The merged result document, or ``None``.
        project_name: The Project cell; the document's own key when empty.
        build_date: The Date cell, already formatted, computed once per render.
        environment: Defaults to :func:`build_environment`.

    Yields:
        ``(output filename, HTML)`` pairs.  Nothing is written.

    Raises:
        PrettyReportBudgetError: If the document carries more distinct tags
            than :data:`MAX_PRETTY_TAGS` or would produce more detail pages
            than :data:`MAX_PRETTY_DETAIL_PAGES`.  Raised on the first
            iteration, before any page is rendered.
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

    # The aggregate fan-out budgets, checked here: both counts are known now
    # and no page has been rendered yet, so a document over either one is
    # refused before this iterator produces anything at all.  Page names rather
    # than subjects, because two subjects hashing to one name are one page.
    _require_counts_within_budget(
        len(tags), len(set(feature_links.values()) | set(tag_links.values()))
    )

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
    # ``pretty/overview_features.html`` documents and reads.  Both pairs carry
    # the same two values, so the page renders the authority's numbers under
    # the names it declares and this writer satisfies either spelling.
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
    file**, so a template fault surfaces before anything is written.  It is
    exactly ``dict(iter_pretty_pages(...))``, so a repeated key keeps its last
    value and the later of two colliding pages is returned.

    The page set is the four overview pages, always -- the exit contract
    requires all four artifacts even when the tag expression selects nothing --
    plus one ``report-feature_<hash>.html`` per emitted feature and one
    ``report-tag_<hash>.html`` per tag of the run.

    Args:
        result_set: The merged result document, or ``None``.
        project_name: The Project cell of the build-info table.
        build_date: The already-formatted Date cell, computed once per render.
        environment: Defaults to :func:`build_environment`.

    Returns:
        Output filename to HTML, in the order the pages are written.

    Raises:
        PrettyReportBudgetError: If the document is over a count budget; see
            :func:`iter_pretty_pages`.  The cumulative byte budget is not
            applied here: this function is the pure render, and the budget is
            about what a publication writes.
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


def _copy_file(
    publication: ArtifactDirectoryPublication, source: Path, relative_name: str
) -> Path:
    """Copy one asset into the publication's staging tree, byte for byte.

    These are the reference generator's own minified files and fonts, so they
    are copied rather than regenerated, re-minified or re-encoded.  What
    changed with the secure publication is *how* the destination is reached,
    and it is two changes in one:

    * the destination is resolved as a **name relative to the staging
      descriptor** the path authority verified, through
      :meth:`~app.utils.paths.ArtifactDirectoryPublication.copy_in`, so no
      component of it is re-resolved from a pathname after the check that
      approved it -- which is what a :func:`shutil.copy2` onto a joined path
      did, and what let a link planted mid-copy redirect the write out of the
      tree (CWE-59/CWE-367); and
    * only the **bytes** travel.  ``copy2`` copies the source's mode and
      timestamps too, so a ``0644`` asset vendored in a wheel arrived
      group-readable and reintroduced exactly the bits the artifact mode
      policy exists to clear (CWE-732).  The copy is created ``0600`` like
      every page beside it.

    An existing file is overwritten in place; nothing is deleted.

    Args:
        publication: The publication whose staging tree receives the copy.
        source: The file to read.  Package data outside the artifact root, so
            reading it is an ordinary read.
        relative_name: Slash-separated destination inside the staging tree,
            such as ``css/cucumber.css``.

    Returns:
        The destination path, for the census :func:`copy_pretty_assets`
        returns.

    Raises:
        app.utils.paths.ArtifactPathError: If the staging tree has not been
            created, if ``relative_name`` is not a usable relative path, or if
            a component of it is a link or is not a directory.
        FileNotFoundError: If ``source`` is absent.
        OSError: If the source cannot be read or the destination written.
    """
    return publication.copy_in(source, relative_name)


def copy_pretty_assets(
    publication: ArtifactDirectoryPublication,
) -> tuple[Path, ...]:
    """Copy the vendored asset set into the publication's staging tree.

    Both sources are package-relative, so the assets travel with an installed
    wheel: :func:`app.utils.paths.vendor_dir`, whose ``css/``, ``js/``,
    ``fonts/`` and ``images/`` sub-directories map 1:1 onto the emitted tree
    and are walked rather than listed, so the vendored set is the emitted set;
    and :func:`~app.utils.paths.static_dir`, for the port's own two files.

    The copy is then held to :data:`REQUIRED_ASSETS` in full, **fonts
    included**: a stylesheet requesting a font is a file this function copied
    in, so a missing font is a published reference to a file known to be
    absent.

    Args:
        publication: The publication being built, from
            :func:`app.utils.paths.begin_directory_publication` with its
            staging tree already created.  A publication rather than a
            directory *path*: every reference in a page is relative to the
            page, so these files have to land in the tree the pages are
            written into -- and that tree is addressed through the verified
            staging descriptor the publication holds, not through a name this
            function could join.  During a publication that tree is the
            staging one, so a missing asset is discovered before anything is
            swapped into place.

    Returns:
        Every path written, sorted, so a caller can log or assert on the set.
        The paths are inside the staging tree, which is where the files are
        until :meth:`~app.utils.paths.ArtifactDirectoryPublication.publish`
        renames it.

    Raises:
        FileNotFoundError: If any member of :data:`REQUIRED_ASSETS` is missing
            afterwards -- every asset a page links by name **and** all eleven
            fonts the copied stylesheets request.  That is a broken
            installation rather than a test outcome: the published tree would
            reference files that are not there, so it is reported as the writer
            failure it is, and the message names every missing file so the
            cause is in the log rather than in a reader's browser console.
        app.utils.paths.ArtifactPathError: If the staging tree has not been
            created, or a destination component is a link or is not a
            directory.
        OSError: If a directory cannot be created or a file cannot be copied.
    """
    written: list[Path] = []

    source_root = vendor_dir()
    # Sorted so the copy order - and therefore any log or error naming a file
    # - is the same on every run and every platform.  ``as_posix`` because a
    # publication names its entries with forward slashes on every platform.
    for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
        written.append(
            _copy_file(
                publication, source, source.relative_to(source_root).as_posix()
            )
        )

    static_root = static_dir()
    for relative_source, relative_destination in PORT_ASSETS:
        source = static_root.joinpath(*relative_source.split("/"))
        written.append(_copy_file(publication, source, relative_destination))

    _require_assets(publication)
    return tuple(sorted(written))


def _require_assets(publication: ArtifactDirectoryPublication) -> None:
    """Verify that every required asset is present in the staging tree.

    Called after the copy and again before the swap, because the two questions
    are different: the first says the copy did its job, the second says
    nothing has gone missing between the copy and the swap.  Both ask
    :meth:`~app.utils.paths.ArtifactDirectoryPublication.has_file`, which
    resolves each name through the held staging descriptor and answers ``True``
    for a regular file only -- so a directory or a link standing where an asset
    belongs is a missing asset, which is what it is to a reader's browser.

    Args:
        publication: The publication being built.

    Raises:
        FileNotFoundError: If any member of :data:`REQUIRED_ASSETS` is absent
            or is not a regular file, naming every one of them.
        app.utils.paths.ArtifactPathError: If the staging tree has not been
            created.
        OSError: If the tree cannot be examined.
    """
    missing = [name for name in REQUIRED_ASSETS if not publication.has_file(name)]
    if missing:
        raise FileNotFoundError(
            f"the generated report tree {publication.staging} would reference "
            f"{len(missing)} asset(s) that are not there: " + ", ".join(missing)
        )


# --------------------------------------------------------------------------- #
# Publication: recovery, scratch and the swap
#
# The two scratch directories a publication creates beside the final tree are
# named by :mod:`app.utils.paths`, from ``PUBLICATION_STAGING_INFIX`` and
# ``PUBLICATION_SUPERSEDED_INFIX``, and both names are dot-prefixed -- which is
# what makes them unreachable over HTTP, since ``resolve_artifact`` rejects any
# request whose path carries a component beginning with a dot, so a partial
# generation cannot be served even while it exists.  Both carry the publishing
# process's id, which keeps two publications in two processes from building in
# one directory and is what ``PublicationScratch.is_own`` reports to
# :func:`_recover_interrupted_publication`.  It is **not** a lock: two
# publications writing one workspace at once would already be contending for
# the artifact itself -- and for the three sibling artifacts, none of which is
# locked either -- so serialising this one writer would buy a guarantee the
# artifact set as a whole does not have.  AAP 0.4.1 has one producer per run,
# driven once from ``app/services/report_service.py``.
# --------------------------------------------------------------------------- #


def _discard_quietly(
    publication: ArtifactDirectoryPublication, name: str
) -> None:
    """Remove one scratch entry of ``publication``, logging rather than raising.

    The tidying form of
    :meth:`~app.utils.paths.ArtifactDirectoryPublication.discard_scratch`, used
    at the two points where a cleanup fault must not become the fault that is
    reported:

    * on the **failure path**, where a publication that is already failing has
      one job left -- leave the previous complete tree in place and report the
      fault that caused it -- and an exception raised while tidying up would
      replace that fault with a less useful one and skip the tidying that
      follows; and
    * on the **success path**, after the swap, where the tree on disk is
      already complete: raising there would have
      ``app/services/report_service.py`` name this writer under the exit
      contract's writer-failure class for an artifact that is in fact
      published, which is a false diagnosis of a complete run.

    What remains behind either way is a dot-prefixed directory that no request
    can reach and that the next publication sweeps, so logging it is the
    proportionate response.  The removal itself is the publication's: it
    refuses any name that is not this tree's own scratch, it is
    descriptor-relative, and a link planted inside the scratch is unlinked
    rather than descended (CWE-59/CWE-22).

    Args:
        publication: The publication the scratch belongs to.
        name: The scratch entry's name -- ``publication.staging.name``,
            ``publication.superseded.name``, or a name from
            :meth:`~app.utils.paths.ArtifactDirectoryPublication.scratch_entries`.
    """
    try:
        publication.discard_scratch(name)
    except OSError:
        logger.exception(
            "Could not remove the report publication directory %s; it is "
            "unreachable over HTTP and the next run removes it",
            publication.final.with_name(name),
        )


def _published_tree_stands(publication: ArtifactDirectoryPublication) -> bool:
    """Answer whether the published tree is in place, without raising.

    :meth:`~app.utils.paths.ArtifactDirectoryPublication.published_exists`
    refuses a link standing where the tree belongs, which is the right answer
    on the way *in* -- it fails the publication before a staging tree is built.
    On the way out of a failure it is the wrong one: the exception being
    reported is the fault that caused the failure, and a cleanup decision must
    not replace it.  So the refusal is logged and read as "the tree is not
    there", which is the conservative branch: the renamed-aside copy is left
    where the log says it is rather than removed.

    Args:
        publication: The publication being cleaned up.

    Returns:
        ``True`` only if the published tree is there and is a directory this
        writer may publish through.
    """
    try:
        return publication.published_exists()
    except OSError:
        logger.exception(
            "Could not establish whether the report tree %s is in place; "
            "leaving the publication scratch beside it alone",
            publication.final,
        )
        return False


def _restore_previous_generation(
    publication: ArtifactDirectoryPublication,
) -> None:
    """Rename a tree that was moved aside back onto the published name.

    The first thing the failure path does, because the one thing that must
    survive a failed publication is the tree that was published before it.  It
    applies only in the window the swap opens: a publication that renamed the
    previous generation aside and then failed before renaming staging into
    place has left the renamed-aside copy as **the only complete generation in
    existence**.

    Nothing is raised.  A restore that cannot be made is logged with both
    paths, so the copy can be renamed back by hand, and the fault that caused
    the failure is still the exception the caller sees.

    Args:
        publication: The publication being cleaned up.
    """
    if not publication.moved_aside or publication.published:
        return
    if _published_tree_stands(publication):
        # Something is already there under the published name, so renaming the
        # copy onto it would fail; it is left where the caller can find it.
        return
    try:
        publication.restore_superseded(publication.superseded.name)
    except OSError:
        logger.exception(
            "Could not restore the previous report tree to %s; it is "
            "intact at %s and can be renamed back by hand",
            publication.final,
            publication.superseded,
        )


def _recover_interrupted_publication(
    publication: ArtifactDirectoryPublication,
) -> None:
    """Put the tree back together after a publication that was killed part-way.

    The swap is two renames, so there is one instant in which the published
    tree has been moved aside and its replacement has not yet taken its place.
    A process killed in that instant leaves no published tree and one
    renamed-aside copy that **is the only complete generation in existence**.
    This function is what makes that recoverable, over
    :meth:`~app.utils.paths.ArtifactDirectoryPublication.published_exists` and
    :meth:`~app.utils.paths.ArtifactDirectoryPublication.scratch_entries` so
    that every entry it acts on was classified against the verified parent
    rather than by a name this module parsed:

    * **an orphaned renamed-aside tree is restored, never removed.**  If the
      published tree is absent and a renamed-aside copy is there, it is renamed
      back first, before anything else in this call touches the filesystem --
      so a publication that then fails on a missing asset or a page write
      leaves that restored tree published, rather than leaving a reader with
      nothing.  A document refused by a fan-out budget, or a render that
      raises, does not reach this point at all: both are settled before the
      publication begins, which is what keeps an over-budget document free of
      filesystem effects, and the interrupted tree is then recovered by the
      next publication that gets this far.
      The most recently modified copy wins, with the name breaking a tie, so
      the choice is deterministic.  A copy carrying another process's
      identifier is eligible too: it is a complete generation of this artifact
      whoever produced it, and the alternative is publishing nothing where a
      report exists.  A copy whose rename back *fails* is left where the log
      says it is, even when it carries this process's own id, because it is
      then still the only complete generation there is;
    * **this call's own two scratch names are cleared**, because a previous run
      of *this* process cannot still be using them and the staging directory
      would otherwise be created on top of a partial tree.  The removal is the
      raising form: a scratch directory that cannot be removed would leave the
      build output holding a dot-directory and the publication building into
      one, which is a genuine fault rather than untidiness;
    * **scratch carrying another process's identifier is reported and left
      alone**, no publisher being able to tell a dead process's leavings from a
      live one's portably; it is dot-prefixed, so no request reaches it.

    Args:
        publication: The publication about to be built, whose own scratch
            names are the ones that may be cleared.

    Raises:
        app.utils.paths.ArtifactPathError: If a link stands where the published
            tree belongs.  Raised here, before a staging tree is built, so a
            redirection out of the artifact root fails the publication rather
            than being renamed aside and replaced.
        OSError: If the parent cannot be listed, or if this process's own
            scratch cannot be removed.
    """
    entries = publication.scratch_entries()
    # A candidate whose restore could not be made, so that the removal below
    # leaves it alone: it is still the only complete generation there is, and
    # the log record above tells an operator where to find it.
    unrestored: str | None = None
    if not publication.published_exists():
        superseded = [
            entry for entry in entries if entry.is_superseded and entry.is_dir
        ]
        if superseded:
            candidate = max(
                superseded, key=lambda entry: (entry.modified_at, entry.name)
            )
            try:
                publication.restore_superseded(candidate.name)
            except OSError:
                unrestored = candidate.name
                logger.exception(
                    "Could not restore the report tree from %s to %s; the "
                    "previous generation is intact there and can be renamed "
                    "back by hand",
                    candidate.path,
                    publication.final,
                )
            else:
                logger.warning(
                    "Restored %s from %s, left behind by an interrupted "
                    "report publication",
                    publication.final,
                    candidate.path,
                )
                entries = publication.scratch_entries()

    for entry in entries:
        if entry.name == unrestored:
            # Skipped even though it carries this process's id: the rename back
            # failed, so removing it here would delete the report the log
            # record above just said was intact.  The failure path of
            # :func:`write_pretty_reports` leaves a copy aside for the same
            # reason, and this is the same rule applied on the way in.
            continue
        if entry.is_own:
            logger.warning(
                "Removing %s, left behind by an interrupted report "
                "publication of this process",
                entry.path,
            )
            publication.discard_scratch(entry.name)
        else:
            logger.warning(
                "Leaving %s where it is: it carries another process's "
                "identifier, so a publication may still own it. It is "
                "unreachable over HTTP, and --clean removes it",
                entry.path,
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
    place.  One
    :class:`~app.utils.paths.ArtifactDirectoryPublication` carries the whole
    sequence, and every filesystem step of it -- the staging directory, each
    asset copy, each page write, both renames and each scratch removal -- is
    made relative to the parent descriptor that object verified when it was
    created, so nothing below is re-resolved from a pathname a link could have
    replaced in the meantime (CWE-59/CWE-367):

    0. the document's fan-out is held to every budget, and the whole page set
       is produced: the count budgets from the model
       (:func:`_require_fan_out_within_budget`), then
       :func:`render_pretty_pages`, whose iterator applies the cumulative
       :data:`MAX_PRETTY_OUTPUT_BYTES` budget as it goes.  All of this happens
       before any path is derived and any directory is created, so a document
       over any of the three is refused without a single filesystem effect;
    1. the publication is begun, which verifies and creates the parent and
       refuses a link standing in for the build output directory, for
       :func:`app.utils.paths.pretty_reports_dir` or for the published tree
       itself; an earlier publication that was killed
       part-way is put back together
       (:func:`_recover_interrupted_publication`) and the staging tree is
       created;
    2. the assets are copied into staging and validated
       (:func:`copy_pretty_assets`);
    3. the pages produced in step 0 -- already rendered and already within
       :data:`MAX_PRETTY_OUTPUT_BYTES` -- are written one file at a time;
    4. the complete inventory in staging is verified: every required asset
       again, and every page the iterator produced;
    5. the published tree is renamed aside, staging is renamed into place
       (:meth:`~app.utils.paths.ArtifactDirectoryPublication.publish`), and
       the renamed-aside copy is deleted.  Two renames rather than one
       replace, because renaming a directory onto an existing directory fails
       on Windows and on POSIX alike and AAP 0.8 requires Windows support.

    Every directory in the published tree is ``0700`` and every page and asset
    in it is ``0600``: a page carries the run's failure text and its embedded
    screenshots, so the artifact is the owner's to read, and the asset copy
    takes the source's bytes without its mode for the same reason
    (CWE-732/CWE-359).

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
        result_set: The merged result document, or ``None``.
        base: Directory the artifact path resolves against; cwd by default.
        directory: An explicit output directory, overriding ``base``.
        project_name: The Project cell; empty by default.
        build_date: The already-formatted Date cell.
        environment: Defaults to :func:`build_environment`.

    Returns:
        The directory written; :func:`app.utils.paths.pretty_reports_html_dir`
        with the default ``base``.

    Raises:
        PrettyReportBudgetError: If the document carries more distinct tags
            than :data:`MAX_PRETTY_TAGS`, would produce more detail pages than
            :data:`MAX_PRETTY_DETAIL_PAGES`, or produces more page bytes than
            :data:`MAX_PRETTY_OUTPUT_BYTES`.  All three are refused before a
            staging path is derived, before a directory is created, before an
            asset is copied and before a page is opened for writing, so a
            refused document leaves the filesystem exactly as it was.
        jinja2.TemplateError: If a page cannot be rendered.
        FileNotFoundError: If a required asset is missing; see
            :func:`copy_pretty_assets`.
        app.utils.paths.ArtifactPathError: If a component from ``target``
            inward is a symbolic link or a junction, if a link stands where the
            published tree belongs, or if a page or asset name cannot be
            verified inside the staging tree.  A redirection out of the
            artifact root is refused rather than written through.
        OSError: If a directory cannot be created, a page cannot be written or
            the swap cannot be made.

        Every one of them propagates, because producing this artifact is the
        writer's contract with the run's exit table, whose writer-failure class
        names the failing writer on stderr.  None of them leaves a partial
        tree: the staging tree is removed, a tree already renamed aside is
        renamed back, and neither scratch directory survives the call.
    """
    final = Path(pretty_reports_html_dir(base) if directory is None else directory)

    # THE WHOLE FAN-OUT IS PRODUCED AND BUDGETED BEFORE THE FILESYSTEM IS
    # TOUCHED, and the order of these two steps is the contract rather than a
    # convenience.  The count budgets come first, from the model alone, so a
    # document asking for more pages than this writer produces is refused
    # without even a staging path having been derived.  Then every page is
    # rendered through :func:`iter_pretty_pages`, which applies the cumulative
    # byte budget as it goes, so a document whose pages together exceed
    # MAX_PRETTY_OUTPUT_BYTES is refused here too -- before the publication,
    # before the asset copy and before a single page is opened for writing.
    # Nothing of an over-budget document reaches the disk, which is what makes
    # the budgets a bound on this writer's I/O and not only on what it
    # publishes.  The cost is the page set in memory for the duration of the
    # write, which the byte budget is itself the bound on.
    _require_fan_out_within_budget(result_set)
    pages = render_pretty_pages(
        result_set,
        project_name=project_name,
        build_date=build_date,
        environment=environment,
    )


    # One publication object for the whole call: it verifies and holds the
    # parent of the published tree, derives the two scratch names, and is the
    # only route from here to the filesystem.  Used as a context manager, so
    # the descriptors are released on every path out - and nothing is removed
    # on the way out, because what should happen to a staging or renamed-aside
    # tree after a failure is decided below rather than by a closing handler.
    with begin_directory_publication(final) as publication:
        _recover_interrupted_publication(publication)

        try:
            publication.create_staging()
            assets = copy_pretty_assets(publication)

            # Keyed by filename rather than appended to, so a detail-name
            # collision is one staged page here as it is one file on disk.
            staged: dict[str, None] = {}
            for filename, html in pages.items():
                # The page is named relative to the verified staging
                # descriptor, never joined onto a path.  newline="\n" so a
                # page written on Windows is byte-identical to one written on
                # Linux: the structure of this artifact must not depend on
                # which side of the pipeline's isUnix() branch produced it.  A
                # repeated filename overwrites in place, which is the
                # detail-page collision rule render_pretty_pages documents.
                with publication.open(filename) as stream:
                    stream.write(html)
                staged[filename] = None
                logger.debug("Staged %s", publication.staging / filename)

            # The inventory check, over staging and before the swap: the assets
            # once more, in case a page write disturbed one, and every page the
            # iterator produced.  Anything missing here means the tree would be
            # published incomplete, which is the one outcome this writer must
            # not produce.
            _require_assets(publication)
            missing_pages = [
                name for name in staged if not publication.has_file(name)
            ]
            if missing_pages:
                raise OSError(
                    f"the staged report tree {publication.staging} is missing "
                    f"{len(missing_pages)} page(s) that were written into it: "
                    + ", ".join(missing_pages)
                )

            # The swap.  Between the two renames the published tree is absent
            # rather than partial, which is the one instant this design cannot
            # remove; both renames are metadata operations made relative to the
            # verified parent, on one filesystem.
            published = publication.publish()
        except BaseException:
            # Restoration comes first and everything else is best-effort: the
            # one thing that must survive a failed publication is the tree that
            # was published before it.
            _restore_previous_generation(publication)
            _discard_quietly(publication, publication.staging.name)
            if _published_tree_stands(publication):
                # The published tree is in place - restored, or newly swapped
                # in before a later step failed - so the copy aside is scratch.
                # When the restore could not be made, the copy aside is the
                # only surviving generation and is deliberately left where the
                # log says it is.
                _discard_quietly(publication, publication.superseded.name)
            raise
        else:
            # The swap has already happened, so the tree on disk is complete
            # and this writer has met its contract.  A cleanup fault from here
            # is therefore logged -- ``app/logging_config.py`` routes it to
            # stderr and the next publication sweeps what it left -- and
            # deliberately not raised: raising would have
            # ``app/services/report_service.py`` name this writer under the
            # exit contract's writer-failure class for an artifact that is in
            # fact published, which is a false diagnosis of a complete run.
            _discard_quietly(publication, publication.superseded.name)

    logger.info(
        "Published %d page(s) and %d asset(s) to %s",
        len(pages),
        len(assets),
        published,
    )
    return published
