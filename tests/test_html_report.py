"""Tests for the single self-contained HTML artifact, ``target/cucumber-reports.html``.

This module is the gate for ``app/reporting/html_report.py``, for the four
``app/templates/artifact/*.html`` templates the page is built from and for the
partials they share, and what it protects is the artifact's whole reason for
existing: **one file, carrying everything, referencing nothing**.  The
destination is ``CukesRunner.java:10`` and the acceptance criteria are AAP
0.3.4's -- one file, no external URL or sibling reference, the declared title
and charset, and every feature, scenario, step, status, timestamp and embedded
screenshot of the result set present in the DOM.  The rest of the shape is
fixed by the writer's own documented contract.

AAP 0.3.4 maps no HTML golden fixture and the committed reference page carries
an unresolved merge block, so every assertion is structural: the page is parsed
with :mod:`html.parser` and interrogated, never compared byte for byte, and no
parsing dependency is added.  Status is read through the badge's visible text,
the half of the contract a reader sees, and an attribute that has to be read is
reached through one named constant rather than spelled at a call site.
``tests/fixtures/sample_results.json`` supplies the shapes it carries; every
other shape is built here, in documents whose intent is visible at the call
site.
"""

from __future__ import annotations

import ast
import errno
import os
import re
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Final

import pytest
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from app.reporting import html_report as writer
from app.reporting.aggregation import STATUS_ALIASES
from app.reporting.html_report import (
    CSS_ASSET_PARTS,
    EMPTY_AGGREGATE_STATUS,
    EMPTY_ELEMENT_STATUS,
    FORBIDDEN_IN_SCRIPT,
    FORBIDDEN_IN_STYLE,
    JS_ASSET_PARTS,
    KNOWN_STATUSES,
    REPORT_TEMPLATE,
    STATUS_PRECEDENCE,
    UNKNOWN_STATUS,
    build_environment,
    build_metadata,
    build_render_context,
    build_summary,
    css_asset_path,
    decorated_features,
    earliest_start,
    emitted_features,
    feature_status,
    inline_asset,
    js_asset_path,
    render_html_report,
    status_token,
    write_html_report,
)
from app.utils import paths

# --------------------------------------------------------------------------- #
# The document's fixed vocabulary
#
# Every literal the page is held to is named once here, so a contract change is
# a one-line change in a file whose name says which artifact it describes.
# --------------------------------------------------------------------------- #

#: The declaration ``html.parser`` reports for ``<!DOCTYPE html>``.
DOCTYPE_DECLARATION: Final[str] = "DOCTYPE html"

#: The document element's language tag.
HTML_LANGUAGE: Final[str] = "en"

#: The page title, quoted from the reference artifact.
DOCUMENT_TITLE: Final[str] = "Cucumber"

#: The title line exactly as the reference indents it: one literal tab.
TAB_INDENTED_TITLE: Final[str] = f"\n\t<title>{DOCUMENT_TITLE}</title>\n"

#: The content-type meta line, likewise tab-indented.
TAB_INDENTED_CONTENT_TYPE: Final[str] = (
    '\n\t<meta content="text/html;charset=utf-8" http-equiv="Content-Type">\n'
)

#: The declared character set, lower-cased for comparison.
DECLARED_CHARSET: Final[str] = "charset=utf-8"

#: The favicon's relationship and the prefix of its inline payload.  The SVG is
#: percent-encoded rather than base64, which is what the reference does.
FAVICON_RELATIONSHIP: Final[str] = "icon"
FAVICON_DATA_PREFIX: Final[str] = "data:image/svg+xml,"
FAVICON_PERCENT_ENCODED_TAG: Final[str] = "%3Csvg"

#: The only two reference forms the page may carry: an inline payload, and a
#: fragment pointing inside the page itself.
DATA_URI_SCHEME: Final[str] = "data:"
FRAGMENT_PREFIX: Final[str] = "#"

#: Absolute schemes that must never appear anywhere in the document.  The
#: favicon's payload percent-encodes its own namespace URL, so neither of these
#: occurs in a conforming page even inside an inline asset.
ABSOLUTE_URL_SCHEMES: Final[tuple[str, ...]] = ("http://", "https://")

#: Protocol-relative prefix, which fetches over the page's own scheme and is an
#: external reference by any other name.
PROTOCOL_RELATIVE_PREFIX: Final[str] = "//"

#: The three merge-conflict markers.  They exist in the unmodified reference
#: checkout's committed artifacts (AAP 0.2.2) and must never be emitted.
CONFLICT_MARKERS: Final[tuple[str, ...]] = ("<<<<<<<", "=======", ">>>>>>>")

#: HTML elements that have no end tag.  Needed because a parser that pushed
#: them onto the open-element stack would report the whole document unclosed.
VOID_ELEMENTS: Final[frozenset[str]] = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: Every attribute whose value is a URL the browser would resolve and fetch.
#: The self-containment walk covers all of them rather than only ``href`` and
#: ``src``, so a regression cannot hide behind a less usual attribute.
URL_BEARING_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    {
        "action",
        "background",
        "cite",
        "data",
        "formaction",
        "href",
        "longdesc",
        "manifest",
        "poster",
        "src",
        "srcset",
        "xlink:href",
    }
)

#: ``url(...)`` inside stylesheet text, which fetches exactly as ``src`` does.
CSS_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"""url\(\s*(?P<quote>['"]?)(?P<target>[^'")]*)(?P=quote)\s*\)""",
    re.IGNORECASE,
)

#: ``@import`` inside stylesheet text, in both its bare and its ``url()`` form.
CSS_IMPORT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"""@import\s+(?:url\(\s*)?['"]?(?P<target>[^'");]+)""",
    re.IGNORECASE,
)

#: The class vocabulary the page is interrogated through, quoted from
#: ``app/static/css/main.css``.
BADGE_CLASS: Final[str] = "tqa-badge"
FEATURE_CLASS: Final[str] = "tqa-feature"
SCENARIO_CLASS: Final[str] = "tqa-scenario"
BACKGROUND_CLASS: Final[str] = "tqa-background"
STEP_CLASS: Final[str] = "tqa-step"
STEP_KEYWORD_CLASS: Final[str] = "tqa-step-keyword"
STEP_NAME_CLASS: Final[str] = "tqa-step-name"
STEP_ARGS_CLASS: Final[str] = "tqa-step-args"
TAG_CLASS: Final[str] = "tqa-tag"
EMPTY_CLASS: Final[str] = "tqa-empty"
ERROR_ELEMENT: Final[str] = "pre"

#: The status attribute, in both spellings.  The shared partials now emit
#: ``data-report-status`` alone -- the consolidation the template and
#: static-asset units owned has landed -- and the legacy ``data-tqa-status``
#: spelling is kept here so that this suite reads a page rendered by either
#: vocabulary.  No test pins one spelling; status itself is asserted through
#: the badge's text, which neither spelling affects.
STATUS_HOOK_ATTRIBUTES: Final[tuple[str, ...]] = (
    "data-report-status",
    "data-tqa-status",
)

#: The containers that carry a rolled-up status and a badge for it.
STATUS_CONTAINER_CLASSES: Final[tuple[str, ...]] = (
    FEATURE_CLASS,
    SCENARIO_CLASS,
    BACKGROUND_CLASS,
    STEP_CLASS,
)

#: The attribute the report script recognises a result screenshot by.  It sits
#: on the **trigger** that opens the lightbox -- the ``<button>``
#: ``partials/lightbox.html`` wraps the thumbnail in -- and not on the ``<img>``
#: itself: the activatable element is what needs the hook, and a button is what
#: gives the thumbnail focusability, Enter and Space for free.  The thumbnail
#: is therefore reached through :func:`screenshot_images`, never by probing the
#: images for this attribute.
SCREENSHOT_ATTRIBUTE: Final[str] = "data-report-screenshot"

#: The attribute carrying a screenshot's own caption on that same trigger.
SCREENSHOT_NAME_ATTRIBUTE: Final[str] = "data-report-screenshot-name"

#: The attribute marking the one lightbox image the page chrome carries.  That
#: element is deliberately emitted with **no** ``src``: the script fills it in
#: from a thumbnail that is already inside the page.
LIGHTBOX_IMAGE_ATTRIBUTE: Final[str] = "data-report-lightbox-image"

#: The status filter control's attribute, emitted only when there is something
#: to filter.
FILTER_ATTRIBUTE: Final[str] = "data-report-filter"

#: A fixed generation time, so two renders of one document are comparable.
#: Equal to the sample result set's own ``generated_at``.
FIXED_GENERATED_AT: Final[str] = "2022-09-07T13:39:12.484Z"

#: One descriptor row of the metadata block.  The block filters its rows on a
#: non-empty value, so the class is what a test counts to see a row omitted.
META_ITEM_CLASS: Final[str] = "tqa-meta-item"

#: The label the metadata block gives the generation-time row.
GENERATED_LABEL: Final[str] = "Report generated"

#: The port's one timestamp shape -- millisecond precision, three fractional
#: digits always, and a literal ``Z``, which is what
#: ``app/reporting/events.py``'s ``format_timestamp`` emits.  Used to assert
#: that a page rendered from a document carrying no timestamp carries none
#: either.
TIMESTAMP_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"
)

#: The modules a clock reading would have to come from, and the calls that
#: would read one.  Both are asserted absent from the writer's source: the
#: generation time it displays is the run's, resolved once before the fan-out.
CLOCK_MODULES: Final[frozenset[str]] = frozenset({"datetime", "time"})
CLOCK_CALL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "now",
        "utcnow",
        "today",
        "fromtimestamp",
        "time",
        "time_ns",
        "monotonic",
        "monotonic_ns",
        "perf_counter",
    }
)

#: The status the result model never produces, used to drive the fallback.
#: It has to be outside both :data:`app.reporting.aggregation.KNOWN_STATUSES`
#: and :data:`app.reporting.aggregation.STATUS_ALIASES`: every name in that
#: alias table -- ``executing`` and ``unknown`` among them -- is a behave
#: status the shared normaliser folds onto a Cucumber token, so none of them
#: reaches the fallback any more.  This word is in neither collection, which is
#: what makes it drive ``UNKNOWN_STATUS``.
GARBAGE_STATUS: Final[str] = "no-such-status"

#: One hostile value, carrying every class of character that has to survive the
#: trip: markup, the three HTML-significant punctuation marks, an engine
#: expression, and text outside ASCII.
HOSTILE_MARKUP: Final[str] = "<script>alert(1)</script>"
HOSTILE_EXPRESSION: Final[str] = "{{ 7*7 }}"
HOSTILE_EXPRESSION_EVALUATED: Final[str] = "49"
HOSTILE_VALUE: Final[str] = (
    f"{HOSTILE_MARKUP} & \" ' {HOSTILE_EXPRESSION} "
    "Veuillez renseigner ce champ. \u00e9\u00fc\u2713"
)

#: What the hostile value would read as had the engine evaluated its
#: expression.  Asserting the absence of *this* string is what proves the
#: expression was not evaluated, without depending on the digits ``49`` being
#: absent from an unrelated duration or count elsewhere on the page.
HOSTILE_VALUE_EVALUATED: Final[str] = HOSTILE_VALUE.replace(
    HOSTILE_EXPRESSION, HOSTILE_EXPRESSION_EVALUATED
)

#: The escaped form autoescaping must produce for the injected markup.
ESCAPED_HOSTILE_MARKUP: Final[str] = "&lt;script&gt;alert(1)&lt;/script&gt;"

#: A tag name built from the hostile value.  Tags are rendered as text, and a
#: name is trimmed before it is rendered, so the value carries no outer
#: whitespace of its own.
HOSTILE_TAG: Final[str] = f"@{HOSTILE_VALUE}"

#: A deterministic, well-formed 1x1 PNG payload, base64 as an embedding carries
#: it.  The same bytes ``tests/conftest.py`` hands its stub driver.
#:
#: "Well-formed" is load-bearing rather than descriptive: every chunk's CRC is
#: correct and the image data inflates to the size its header declares, so the
#: payload survives the inline-PNG contract in
#: ``app/reporting/screenshots.py`` that ``app/reporting/aggregation.py`` puts
#: every attachment through before this writer's templates see it.  A payload
#: that only *looked* like a PNG would be discarded there, and every
#: assertion here about a rendered screenshot would pass vacuously.
PNG_BASE64: Final[str] = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP4z8DwHwAFAAH/"
    "VscvDQAAAABJRU5ErkJggg=="
)

#: The media type of a result screenshot, and one that is not an image at all.
PNG_MIME_TYPE: Final[str] = "image/png"
NON_IMAGE_MIME_TYPE: Final[str] = "text/plain"

#: The internal schema's own key names, quoted from ``app/reporting/events.py``
#: so that no shape is spelled out twice in this file.
ELEMENT_TYPE_SCENARIO: Final[str] = "scenario"
ELEMENT_TYPE_BACKGROUND: Final[str] = "background"
SCENARIO_KEYWORD: Final[str] = "Scenario"
BACKGROUND_KEYWORD: Final[str] = "Background"
FEATURE_KEYWORD: Final[str] = "Feature"

#: A nanosecond duration for a synthetic step.  A real integer, because the
#: step partial renders only an integer duration.
SYNTHETIC_DURATION: Final[int] = 1_500_000_000

#: The attribute marking a subtree the report script's status filter hides.
#: The element is this page's filter unit, so nothing inside one carries it.
FILTERABLE_ATTRIBUTE: Final[str] = "data-report-filterable"

#: The hook-report block: the container class, and the fragment its accessible
#: name always carries.  Together they are how a test finds the hook blocks of
#: one element without depending on their position in the markup, and without
#: mistaking the run's environment descriptor -- which uses the same class --
#: for one.  The element template names each block for its own hook, so the
#: name reads "After hook result for Scenario: <name>".
META_CLASS: Final[str] = "tqa-meta"
HOOK_REPORT_LABEL_INFIX: Final[str] = " result for "

#: The two hook groups' labels, as the element template writes them.
BEFORE_HOOK_LABEL: Final[str] = "Before hook"
AFTER_HOOK_LABEL: Final[str] = "After hook"

#: Where the port's own scenario-lifecycle hook lives, in the dotted form the
#: model records and ``app/reporting/events.py`` defaults to.
AFTER_HOOK_LOCATION: Final[str] = "features.environment.after_scenario"

#: A hook duration distinct from every step duration on the page, so a test
#: asserting the hook's own duration is absent cannot be satisfied by a
#: coincidence with a step's.
HOOK_DURATION: Final[int] = 412_000_000

#: behave's own hook-failure status.  It is outside Cucumber's vocabulary, so
#: the status normaliser answers ``unknown`` for it -- which is why the page
#: carries the recorded word as text beside the implementation.
BEHAVE_HOOK_ERROR_STATUS: Final[str] = "hook_error"

# --------------------------------------------------------------------------- #
# The publication contract's vocabulary
#
# ``write_html_report`` no longer names, creates or removes a temporary of its
# own: the whole sequence belongs to
# :func:`app.utils.paths.publish_artifact_file`, so what is named here is what
# an observer outside the writer can see -- the shape of the authority's
# temporary, the platform features the hostile cases need, and the file and
# directory a refusal has to leave untouched.
# --------------------------------------------------------------------------- #

#: Whether this platform can create a symbolic link.  The hostile-link cases
#: have nothing to plant without one, exactly as ``tests/test_paths.py``
#: guards its own.
SYMLINKS_AVAILABLE: Final[bool] = hasattr(os, "symlink")

#: Whether this platform can create a hard link, for the destination case no
#: symlink check can see.
HARD_LINKS_AVAILABLE: Final[bool] = hasattr(os, "link")

#: Whether a POSIX permission mode means anything here.  Windows expresses
#: permissions as ACLs, so the owner-only policy has nothing to assert there.
FILE_MODES_AVAILABLE: Final[bool] = hasattr(os, "fchmod")

#: How the path authority's publication temporary is recognised from outside:
#: dot-prefixed, so ``GET /artifacts/<name>`` cannot serve it while it exists,
#: and suffixed so it is unmistakable in a directory listing.
TEMPORARY_PREFIX: Final[str] = "."
TEMPORARY_SUFFIX: Final[str] = ".partial"

#: A file outside the artifact root for a hostile link to point at, and content
#: distinctive enough that finding it altered -- or finding it gone -- is
#: unambiguous.
OUTSIDE_FILE_NAME: Final[str] = "outside-the-root.html"
OUTSIDE_FILE_CONTENT: Final[str] = "<html>not the artifact</html>\n"

#: The directory a hostile ``target/`` link points at.  The review's probe for
#: this finding wrote the report into exactly such a directory, which is why
#: every refusal test asserts it empty afterwards.
OUTSIDE_DIR_NAME: Final[str] = "outside-the-root"

# --------------------------------------------------------------------------- #
# What the sample document is, pinned
#
# Read off ``tests/fixtures/sample_results.json`` once and asserted as fixed
# numbers, because a count derived from the document at run time cannot detect
# a document, or a selector, that lost something.  These are the figures the
# content assertions are held to; ``select_emitted_features`` below reproduces
# the rule that produces them, independently of the writer.
# --------------------------------------------------------------------------- #

#: The four features the sample emits, by URI, in source order.
SAMPLE_EMITTED_FEATURE_URIS: Final[tuple[str, ...]] = (
    "file:features/Contact.feature",
    "file:features/Crm.feature",
    "file:features/Inventory.feature",
    "file:features/Sales.feature",
)

#: The same four, as the heading each one is expected to carry.  Note the first
#: and third are identical: Contact and Inventory share a title in the source
#: suite, and AAP 0.2.2 preserves that rather than disambiguating it -- which is
#: exactly why the sentinels below are needed as well.
SAMPLE_EMITTED_FEATURE_TITLES: Final[tuple[str, ...]] = (
    "Feature: Testinium app Inventory feature",
    "Feature: Testinium app CRM Module",
    "Feature: Testinium app Inventory feature",
    "Feature: .... app Sales feature",
)

SAMPLE_EMITTED_FEATURE_COUNT: Final[int] = 4

#: Background occurrences and scenarios together, which is what the page shows
#: as third-level headings: 1 + 8 + 1 + 3.
SAMPLE_EMITTED_ELEMENT_COUNT: Final[int] = 13

#: Scenarios alone, Background occurrences excluded: 1 + 4 + 1 + 3.
SAMPLE_SELECTED_SCENARIO_COUNT: Final[int] = 9

#: Steps across every emitted element: 3 + 12 + 5 + 8.
SAMPLE_EMITTED_STEP_COUNT: Final[int] = 28

#: One text per emitted feature that appears in that feature and in no other.
#: A scenario name rather than a feature name, because two feature names
#: collide.  This is what makes "feature X is on the page" assertable at all.
SAMPLE_FEATURE_SENTINELS: Final[dict[str, str]] = {
    "file:features/Contact.feature": (
        "Verify that the user can delete a contact from 2 different side"
    ),
    "file:features/Crm.feature": "User can create pipeline in the displayed dashboard",
    "file:features/Inventory.feature": (
        "Verify that User can reach New Products Form by clicking"
    ),
    "file:features/Sales.feature": "from search bar.",
}


# --------------------------------------------------------------------------- #
# The independent selection oracle
# --------------------------------------------------------------------------- #


def _oracle_is_background(element: dict[str, Any]) -> bool:
    """Answer whether ``element`` is a Background occurrence.

    Deliberately a local reading rather than the writer's own predicate: the
    whole point of this oracle is to be a second opinion.
    """
    return str(element.get("type", "")) == ELEMENT_TYPE_BACKGROUND


def select_emitted_features(document: Any) -> list[dict[str, Any]]:
    """Return the features the artifact should render, by this module's reading.

    Implements the two rules ``app/reporting/html_report.py`` documents, from
    the documentation rather than from the code:

    * a Background occurrence and the scenario it precedes form one unit and
      share its fate, so a unit holding an element with an explicit
      ``"selected": False`` is dropped whole;
    * a feature left with no test case is dropped altogether, a feature left
      with nothing but Background occurrences included, because an occurrence
      is emitted *for* a test case.

    :param document: A merged result document.
    :returns: The features to render, in source order, each with only its
        surviving elements.  The input is never mutated.
    """
    kept: list[dict[str, Any]] = []
    for feature in document.get("features", []):
        units: list[list[dict[str, Any]]] = []
        for element in feature.get("elements", []):
            if _oracle_is_background(element):
                units.append([element])
            elif units and len(units[-1]) == 1 and _oracle_is_background(units[-1][0]):
                units[-1].append(element)
            else:
                units.append([element])
        surviving = [
            element
            for unit in units
            if all(member.get("selected") is not False for member in unit)
            for element in unit
        ]
        if any(not _oracle_is_background(element) for element in surviving):
            kept.append({**feature, "elements": surviving})
    return kept


def oracle_element_count(features: Sequence[dict[str, Any]]) -> int:
    """Count Background occurrences and scenarios across ``features``."""
    return sum(len(feature["elements"]) for feature in features)


def oracle_scenario_count(features: Sequence[dict[str, Any]]) -> int:
    """Count scenarios alone across ``features``."""
    return sum(
        1
        for feature in features
        for element in feature["elements"]
        if not _oracle_is_background(element)
    )


def oracle_step_count(features: Sequence[dict[str, Any]]) -> int:
    """Count steps across every element of ``features``."""
    return sum(
        len(element["steps"])
        for feature in features
        for element in feature["elements"]
    )


# --------------------------------------------------------------------------- #
# The parser
#
# A tree small enough to read in one sitting and complete enough to answer
# every question the contracts below ask: which elements exist, what their
# attributes are, what text a subtree contains, and -- the reason a regex would
# not do -- whether the document is well formed at all.
#
# ``convert_charrefs`` is left on, so an escaped value arrives in ``text`` as
# the author wrote it.  That is what lets one assertion state that a hostile
# value was escaped in the source and another that it survived intact in the
# DOM.  Inside ``<style>`` and ``<script>`` the parser switches to raw text and
# converts nothing, so the inlined assets are compared against their files
# exactly as they sit on disk.
# --------------------------------------------------------------------------- #


class Element:
    """One parsed element: its tag, its attributes, its children and its text."""

    def __init__(
        self,
        tag: str,
        attributes: dict[str, str],
        parent: Element | None = None,
    ) -> None:
        """Store the element's own identity and its position in the tree.

        :param tag: The lower-cased tag name.
        :param attributes: The attributes, lower-cased names to values; a
            valueless attribute such as ``data-report-detail`` maps to ``""``,
            so membership answers "is it present" and the value answers "what
            does it say".
        :param parent: The enclosing element, or ``None`` for the root.
        """
        self.tag = tag
        self.attributes = attributes
        self.parent = parent
        self.children: list[Element] = []
        #: Text and child elements interleaved in document order, which is what
        #: makes :attr:`text` return a subtree's reading order rather than its
        #: text and its children's text in two separate runs.
        self._pieces: list[str | Element] = []

    def add_text(self, data: str) -> None:
        """Append a run of character data to this element's content."""
        self._pieces.append(data)

    def add_child(self, child: Element) -> None:
        """Append a child element, keeping it in document order."""
        self.children.append(child)
        self._pieces.append(child)

    @property
    def text(self) -> str:
        """This element's text content, descendants included, verbatim."""
        return "".join(
            piece if isinstance(piece, str) else piece.text for piece in self._pieces
        )

    @property
    def normalized_text(self) -> str:
        """:attr:`text` with runs of whitespace collapsed to single spaces.

        The form to compare a heading or a badge against: the templates lay
        markup out for a reader diffing the emitted source, so the newlines and
        indentation between two inline elements are presentation rather than
        content.
        """
        return " ".join(self.text.split())

    @property
    def classes(self) -> tuple[str, ...]:
        """The element's class tokens, in source order."""
        return tuple(self.attributes.get("class", "").split())

    @property
    def status_hook(self) -> str | None:
        """The status this element carries, under whichever hook name is used.

        Both spellings are accepted deliberately; see
        :data:`STATUS_HOOK_ATTRIBUTES`.

        :returns: The token, or ``None`` when the element carries no status.
        """
        for name in STATUS_HOOK_ATTRIBUTES:
            if name in self.attributes:
                return self.attributes[name]
        return None

    def walk(self) -> Iterator[Element]:
        """Yield this element and every descendant, in document order."""
        yield self
        for child in self.children:
            yield from child.walk()

    def descendants(self, tag: str) -> list[Element]:
        """Every element in this subtree with the given tag, in document order."""
        return [element for element in self.walk() if element.tag == tag]

    def with_class(self, name: str) -> list[Element]:
        """Every element in this subtree carrying the class token ``name``."""
        return [element for element in self.walk() if name in element.classes]

    def __repr__(self) -> str:
        """A short identification, for a failure message that has to be read."""
        return f"<{self.tag} {self.attributes!r}>"


class ParsedDocument(HTMLParser):
    """A whole parsed page, plus the three records that prove it is well formed.

    :attr:`stray_end_tags`, :attr:`mismatched_end_tags` and
    :attr:`unclosed_elements` are what turn "the page is not malformed" into an
    assertion rather than an impression, and all three are needed because they
    catch different corruptions.  A stray end tag has nothing open to close.  A
    mismatched one closes an outer element while an inner one is still open --
    which is what a *missing* end tag looks like from here, since an end tag
    absorbs everything opened after it and would otherwise pass unnoticed.  An
    unclosed element is one still open when the document ended.  An inline asset
    that closed its own block early produces the first or the second; a template
    that lost an end tag produces the second.
    """

    def __init__(self) -> None:
        """Start with an empty document and nothing open but the root."""
        super().__init__(convert_charrefs=True)
        self.root = Element("#document", {})
        self.declarations: list[str] = []
        self.comments: list[str] = []
        self.stray_end_tags: list[str] = []
        self.mismatched_end_tags: list[tuple[str, str]] = []
        self._open: list[Element] = [self.root]

    @property
    def unclosed_elements(self) -> list[str]:
        """The tags still open when the document ended."""
        return [element.tag for element in self._open[1:]]

    def handle_decl(self, decl: str) -> None:
        """Record a markup declaration -- in practice, the doctype."""
        self.declarations.append(decl)

    def handle_comment(self, data: str) -> None:
        """Record a comment, so a leaked template note can be asserted on."""
        self.comments.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open an element, and push it unless its tag forbids an end tag."""
        element = Element(
            tag,
            {name.lower(): (value if value is not None else "") for name, value in attrs},
            self._open[-1],
        )
        self._open[-1].add_child(element)
        if tag not in VOID_ELEMENTS:
            self._open.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle ``<x />``: open the element and close it immediately.

        Overridden rather than inherited because the inherited implementation
        calls :meth:`handle_endtag`, which for a void element written in the
        self-closing form would look like an end tag with nothing open.
        """
        self.handle_starttag(tag, attrs)
        if tag not in VOID_ELEMENTS and self._open[-1].tag == tag:
            self._open.pop()

    def handle_endtag(self, tag: str) -> None:
        """Close the nearest open element with this tag, or record the fault.

        Closing an element while a descendant is still open is recorded as a
        mismatch rather than silently absorbed, because that absorption is
        exactly how a dropped end tag hides: the enclosing element's end tag
        closes both and the tree still looks balanced.
        """
        if tag in VOID_ELEMENTS:
            # ``<br/>`` and friends reach here from the self-closing form; HTML
            # has no end tag for them, so there is nothing to close and nothing
            # is wrong.
            return
        for depth in range(len(self._open) - 1, 0, -1):
            if self._open[depth].tag == tag:
                if depth != len(self._open) - 1:
                    self.mismatched_end_tags.append((self._open[-1].tag, tag))
                del self._open[depth:]
                return
        self.stray_end_tags.append(tag)

    def handle_data(self, data: str) -> None:
        self._open[-1].add_text(data)


def parse_html(markup: str) -> ParsedDocument:
    """Parse ``markup`` into a :class:`ParsedDocument`.

    :param markup: A whole rendered page.
    :returns: The parsed document, fed and closed.
    """
    document = ParsedDocument()
    document.feed(markup)
    document.close()
    return document


def markup_faults(document: ParsedDocument) -> list[str]:
    """Every well-formedness fault in ``document``, as text a reader can act on.

    One helper rather than three assertions at each call site, so that every
    page this module renders is held to the same standard and a new fault class
    added here reaches all of them at once.

    :param document: A parsed page.
    :returns: One description per fault; empty for a well-formed document.
    """
    faults = [f"stray </{tag}>" for tag in document.stray_end_tags]
    faults.extend(
        f"</{closed}> closed while <{still_open}> was open"
        for still_open, closed in document.mismatched_end_tags
    )
    faults.extend(f"<{tag}> never closed" for tag in document.unclosed_elements)
    return faults


def badge_labels(scope: Element) -> list[str]:
    """The text of every status badge in ``scope``, in document order."""
    return [badge.normalized_text for badge in scope.with_class(BADGE_CLASS)]


def first_badge_label(scope: Element) -> str | None:
    """The text of the first status badge in ``scope``, or ``None``.

    "First in document order" is what makes this the badge belonging to
    ``scope`` itself: a feature's own badge sits in its heading, which precedes
    every scenario inside it, and a scenario's precedes every step inside it.
    """
    labels = badge_labels(scope)
    return labels[0] if labels else None


def status_containers(document: ParsedDocument) -> list[Element]:
    """Every feature, scenario, background and step container on the page."""
    return [
        element
        for element in document.root.walk()
        if any(name in element.classes for name in STATUS_CONTAINER_CLASSES)
    ]


def screenshot_triggers(scope: Element) -> list[Element]:
    """Every element in ``scope`` that opens a result screenshot.

    The single place this suite names *where* the screenshot hook lives, so the
    tests below read a screenshot the way the report script does -- by finding
    the activatable trigger -- and none of them has to know whether the hook
    sits on the trigger, the figure or the image.

    :param scope: A subtree to search, usually ``document.root``.
    :returns: The elements carrying :data:`SCREENSHOT_ATTRIBUTE`, in document
        order.
    """
    return [
        element
        for element in scope.walk()
        if SCREENSHOT_ATTRIBUTE in element.attributes
    ]


def screenshot_images(scope: Element) -> list[Element]:
    """The thumbnail ``<img>`` of every result screenshot in ``scope``.

    One image per trigger, taken from inside it: the payload, the accessible
    name and the dimensions live on the image, while the hook and the trigger's
    own accessible name live on the button around it.  A trigger carrying no
    image would be a screenshot with nothing to show, so it contributes
    nothing here and the count assertions at the call sites catch it.

    :param scope: A subtree to search, usually ``document.root``.
    :returns: The images, in document order.
    """
    return [
        image
        for trigger in screenshot_triggers(scope)
        for image in trigger.descendants("img")
    ]


def reference_candidates(document: ParsedDocument) -> list[tuple[str, str, str]]:
    """Every URL the browser would resolve, as ``(tag, attribute, value)``.

    ``srcset`` is split on commas and each candidate's descriptor dropped, so a
    responsive image set is checked entry by entry rather than as one opaque
    string.  A present-but-empty value is reported rather than skipped: an empty
    ``src`` re-requests the containing document, which is a fetch.
    """
    found: list[tuple[str, str, str]] = []
    for element in document.root.walk():
        for name, value in element.attributes.items():
            if name not in URL_BEARING_ATTRIBUTES:
                continue
            if name == "srcset":
                for part in value.split(","):
                    candidate = part.strip()
                    if candidate:
                        found.append((element.tag, name, candidate.split(" ")[0]))
                    else:
                        found.append((element.tag, name, ""))
                continue
            found.append((element.tag, name, value.strip()))
    return found


def is_self_contained(reference: str) -> bool:
    """Report whether ``reference`` resolves without leaving the page.

    Two forms qualify and nothing else does: an inline ``data:`` payload, and a
    fragment pointing inside this document.  A sibling filename, an absolute
    URL, a protocol-relative URL and an empty value all fail.
    """
    return reference.startswith((DATA_URI_SCHEME, FRAGMENT_PREFIX))


def external_references(document: ParsedDocument) -> list[tuple[str, str, str]]:
    """Every reference on the page that would be fetched from outside it."""
    return [
        candidate
        for candidate in reference_candidates(document)
        if not is_self_contained(candidate[2])
    ]


def stylesheet_references(style_text: str) -> list[str]:
    """Every ``url(...)`` and ``@import`` target in stylesheet text.

    Scanned over the stylesheet rather than over the whole document on purpose:
    the inlined behaviour script's comments legitimately mention addressing
    schemes in prose -- how a report is opened from a build workspace, and the
    framework helper the served copy of the script is resolved by -- and a
    text-level scan of a comment is not a fetch.  What CSS declares here *is* a
    fetch, so this is where the scan belongs.
    """
    targets = [
        match.group("target").strip()
        for match in CSS_URL_PATTERN.finditer(style_text)
    ]
    targets.extend(
        match.group("target").strip()
        for match in CSS_IMPORT_PATTERN.finditer(style_text)
    )
    return targets


def file_census(root: Path) -> set[str]:
    """Every path under ``root``, relative and slash-separated.

    Directories are included, which is deliberate: the artifact's writer is
    allowed to create ``target/`` and nothing else, and a census that listed
    only files could not tell a created directory from an absent one.
    """
    return {
        entry.relative_to(root).as_posix() for entry in root.rglob("*")
    }


def single_page_census(root: Path) -> set[str]:
    """The census of a root holding the build directory and the page alone.

    Named once, because every publication test asserts it and each of them is
    really asserting the same two facts: the writer created the build directory
    and the page it is allowed to create, and no publication temporary is left
    behind -- after a successful write, and after a refused one, where the entry
    at the destination is whatever the test planted there.
    """
    return {
        paths.target_root(root).relative_to(root).as_posix(),
        paths.cucumber_reports_html_path(root).relative_to(root).as_posix(),
    }


def permission_mode(path: Path) -> int:
    """The POSIX permission bits of ``path``, special bits included.

    ``stat.S_IMODE`` rather than a mask of ``st_mode``, so a set-group-id build
    directory -- which a shared checkout can legitimately carry -- is visible
    to the caller rather than silently dropped from the comparison.
    """
    return stat.S_IMODE(path.stat().st_mode)


# --------------------------------------------------------------------------- #
# Synthetic documents
#
# The sample result set covers what a real run produces.  These builders cover
# what it cannot: the three statuses no scenario in the suite reached, the
# fallback for a status the model never produced, values chosen to attack the
# templates, and every empty shape.  Each is a plain mapping in the internal
# schema ``app/reporting/events.py`` owns.
# --------------------------------------------------------------------------- #


def make_step(
    name: str,
    *,
    keyword: str = "Given ",
    status: Any = "passed",
    duration: int | None = SYNTHETIC_DURATION,
    error_message: str | None = None,
    location: str | None = None,
    arguments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one step in the internal schema.

    :param name: The step text, as the source writes it.
    :param keyword: The Gherkin keyword, trailing space included, which is how
        the model carries it and what the step partial trims.
    :param status: The result status.  Anything at all: the point of several
        tests below is what the page does with a value the model never produced.
    :param duration: Nanoseconds, or ``None`` to omit the key entirely.
    :param error_message: Failure text, omitted when ``None``.
    :param location: The step implementation's location, omitted when ``None``.
    :param arguments: Matched arguments, each ``{"val": ..., "offset": ...}``.
    :returns: The step mapping.
    """
    result: dict[str, Any] = {"status": status}
    if duration is not None:
        result["duration"] = duration
    if error_message is not None:
        result["error_message"] = error_message
    match: dict[str, Any] = {}
    if location is not None:
        match["location"] = location
    if arguments is not None:
        match["arguments"] = arguments
    return {"keyword": keyword, "name": name, "result": result, "match": match}


def make_element(
    name: str,
    steps: list[dict[str, Any]],
    *,
    element_type: str = ELEMENT_TYPE_SCENARIO,
    keyword: str = SCENARIO_KEYWORD,
    **extra: Any,
) -> dict[str, Any]:
    """Build one scenario or background element in the internal schema.

    :param name: The element name, as the source writes it.
    :param steps: Its steps, in line order.  An empty list is a legitimate
        shape -- ``EmployeeFc.feature`` declares a background with no body.
    :param element_type: ``"scenario"`` or ``"background"``.
    :param keyword: The Gherkin keyword for the heading.
    :param extra: Further keys -- ``start_timestamp``, ``tags``, ``after``,
        ``selected``, ``line``, ``id`` -- merged in as given.
    :returns: The element mapping.
    """
    return {
        "type": element_type,
        "keyword": keyword,
        "name": name,
        "steps": steps,
        **extra,
    }


def make_feature(
    name: str,
    elements: list[dict[str, Any]],
    *,
    keyword: str = FEATURE_KEYWORD,
    **extra: Any,
) -> dict[str, Any]:
    """Build one feature in the internal schema.

    :param name: The feature name, as the source writes it.
    :param elements: Its elements, in document order.
    :param keyword: The Gherkin keyword for the heading.
    :param extra: Further keys -- ``uri``, ``line``, ``id``, ``tags``,
        ``description`` -- merged in as given.
    :returns: The feature mapping.
    """
    return {"keyword": keyword, "name": name, "elements": elements, **extra}


def make_result_set(*features: dict[str, Any]) -> dict[str, Any]:
    """Wrap features in a merged result document.

    :param features: The features, in source order.
    :returns: The document, carrying the one key every writer reads.
    """
    return {"features": list(features)}


def make_hook(
    *,
    status: Any = "passed",
    location: str | None = AFTER_HOOK_LOCATION,
    duration: int | None = HOOK_DURATION,
    error_message: str | None = None,
    embeddings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one hook entry, in the shape ``app/reporting/events.py`` records.

    :param status: The hook's own outcome.  Untyped at the call site because
        behave's vocabulary reaches past Cucumber's -- ``hook_error`` and
        ``cleanup_error`` are the values a real failing hook carries.
    :param location: Dotted path of the hook implementation; ``None`` yields an
        empty ``match``, which is how the model records a hook it has no path
        for.
    :param duration: Nanoseconds, or ``None`` to omit the key.
    :param error_message: The hook's failure text, omitted when ``None``.
    :param embeddings: Attachments hanging off the hook, omitted when ``None``.
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
    if embeddings is not None:
        entry["embeddings"] = embeddings
    return entry


def hook_reports(scope: Element) -> list[Element]:
    """Every hook-report block in ``scope``, in document order.

    Found by the container class together with the accessible name the element
    template gives it, so the environment descriptor -- which uses the same
    class -- is never mistaken for one.  One block per reported hook.

    :param scope: A subtree to search, usually ``document.root``.
    :returns: The blocks, in document order.
    """
    return [
        element
        for element in scope.with_class(META_CLASS)
        if HOOK_REPORT_LABEL_INFIX in element.attributes.get("aria-label", "")
    ]


def make_embedding(
    *,
    mime_type: Any = PNG_MIME_TYPE,
    data: Any = PNG_BASE64,
    name: str | None = None,
) -> dict[str, Any]:
    """Build one attachment, in the after-hook shape a failure produces.

    Each argument is deliberately untyped at the call site so that a malformed
    attachment -- a missing payload, a blank one, a media type that is not an
    image -- can be built as easily as a well-formed one.
    """
    embedding: dict[str, Any] = {"mime_type": mime_type}
    if data is not None:
        embedding["data"] = data
    if name is not None:
        embedding["name"] = name
    return embedding


def hostile_result_document() -> dict[str, Any]:
    """A result set whose every renderable value is the hostile one.

    Feature name, description, source, identifier and tag; scenario name,
    description, identifier and tag; step keyword, name, matched argument,
    implementation location and failure text; and the embedding's caption.  One
    document, so a single render exercises every interpolation point at once and
    a value escaped in six places but not the seventh cannot pass.
    """
    step = make_step(
        HOSTILE_VALUE,
        keyword=f"{HOSTILE_VALUE} ",
        status="failed",
        error_message=HOSTILE_VALUE,
        location=HOSTILE_VALUE,
        arguments=[{"val": HOSTILE_VALUE, "offset": 0}],
    )
    element = make_element(
        HOSTILE_VALUE,
        [step],
        start_timestamp="2022-09-07T13:37:26.297Z",
        line=4,
        id=HOSTILE_VALUE,
        description=HOSTILE_VALUE,
        tags=[{"name": HOSTILE_TAG}],
        after=[{"embeddings": [make_embedding(name=HOSTILE_VALUE)]}],
    )
    feature = make_feature(
        HOSTILE_VALUE,
        [element],
        uri=f"file:features/{HOSTILE_VALUE}",
        line=1,
        id=HOSTILE_VALUE,
        description=HOSTILE_VALUE,
        tags=[{"name": HOSTILE_TAG}],
    )
    return make_result_set(feature)


# --------------------------------------------------------------------------- #
# Fixtures
#
# All of them build on ``tests/conftest.py``'s ``sample_result_set`` and
# ``tmp_artifact_root``; nothing here redefines a shared fixture.
# --------------------------------------------------------------------------- #


@pytest.fixture
def sample_document(sample_result_set: Any) -> str:
    """The sample result set rendered, with the generation time pinned."""
    return render_html_report(sample_result_set, generated_at=FIXED_GENERATED_AT)


@pytest.fixture
def sample_dom(sample_document: str) -> ParsedDocument:
    """The sample render, parsed."""
    return parse_html(sample_document)


@pytest.fixture
def hostile_document() -> str:
    return render_html_report(
        hostile_result_document(), generated_at=FIXED_GENERATED_AT
    )


@pytest.fixture
def hostile_dom(hostile_document: str) -> ParsedDocument:
    return parse_html(hostile_document)


@pytest.fixture
def every_status_document() -> str:
    """One scenario per known status, plus one carrying an unproduced status.

    Eight scenarios, so the page has to present all seven tokens the result
    model produces and the fallback for one it does not.
    """
    elements = [
        make_element(f"scenario reported {status}", [make_step("a step", status=status)])
        for status in (*KNOWN_STATUSES, GARBAGE_STATUS)
    ]
    return render_html_report(
        make_result_set(make_feature("Every status", elements)),
        generated_at=FIXED_GENERATED_AT,
    )


# --------------------------------------------------------------------------- #
# Document structure
# --------------------------------------------------------------------------- #


def test_document_is_one_html_document_with_the_html5_doctype(
    sample_document: str, sample_dom: ParsedDocument
) -> None:
    """One doctype, one document element, and nothing outside it.

    "One file" is the artifact's contract, and this is its markup half: a page
    that declared its doctype twice, or carried two document elements, would
    render in quirks mode or lose half its content depending on the browser.
    """
    assert sample_dom.declarations == [DOCTYPE_DECLARATION]
    root_elements = [element.tag for element in sample_dom.root.children]
    assert root_elements == ["html"]
    assert sample_document.count("<html") == 1
    assert sample_document.count("</html>") == 1
    assert sample_document.startswith(f"<!{DOCTYPE_DECLARATION}>\n")


def test_document_element_declares_the_english_language(
    sample_dom: ParsedDocument,
) -> None:
    """``<html lang="en">``, which a screen reader needs to pick a voice."""
    html_elements = sample_dom.root.descendants("html")
    assert len(html_elements) == 1
    assert html_elements[0].attributes.get("lang") == HTML_LANGUAGE


def test_document_carries_exactly_one_title_and_it_is_cucumber(
    sample_dom: ParsedDocument,
) -> None:
    """The title is the reference artifact's, exactly, and there is one of it."""
    titles = sample_dom.root.descendants("title")
    assert len(titles) == 1
    assert titles[0].text == DOCUMENT_TITLE


def test_document_declares_utf8_through_a_content_type_meta(
    sample_dom: ParsedDocument,
) -> None:
    """The charset is declared, and declared the way the reference declares it.

    The page carries French validation text and apostrophes in scenario names,
    and it is opened straight from a file rather than served with a header, so
    the declaration in the document is the only thing that fixes its encoding.
    """
    metas = sample_dom.root.descendants("meta")
    content_type_metas = [
        meta
        for meta in metas
        if meta.attributes.get("http-equiv", "").lower() == "content-type"
    ]
    assert len(content_type_metas) == 1
    content = content_type_metas[0].attributes.get("content", "").lower()
    assert DECLARED_CHARSET in content.replace(" ", "")


def test_head_keeps_the_reference_tab_indentation(sample_document: str) -> None:
    """The title and the meta are indented with one literal tab each.

    Measured from the reference artifact and stated in the writer's own module
    docstring.  It is asserted because this page is a document people read and
    diff, so its emitted source is part of its shape rather than an accident of
    whitespace control.
    """
    assert TAB_INDENTED_TITLE in sample_document
    assert TAB_INDENTED_CONTENT_TYPE in sample_document


def test_favicon_is_a_percent_encoded_inline_svg(sample_dom: ParsedDocument) -> None:
    """The one ``link`` on the page is an inline SVG icon and nothing else.

    A favicon is the reference's single most easily overlooked external
    reference, and the shape it uses -- percent-encoded SVG rather than base64 --
    is what keeps the payload legible in the emitted source.
    """
    links = sample_dom.root.descendants("link")
    assert len(links) == 1
    icon = links[0]
    assert icon.attributes.get("rel") == FAVICON_RELATIONSHIP
    href = icon.attributes.get("href", "")
    assert href.startswith(FAVICON_DATA_PREFIX)
    assert FAVICON_PERCENT_ENCODED_TAG in href


def test_stylesheet_and_script_are_inlined_into_the_page(
    sample_dom: ParsedDocument,
) -> None:
    """One inline ``style`` and one inline ``script``, both non-empty.

    The script is asserted to carry no ``src``, because a script element with
    one would fetch and the block's own text would never run -- the exact
    regression that turns a self-contained page into a broken one.
    """
    styles = sample_dom.root.descendants("style")
    scripts = sample_dom.root.descendants("script")
    assert len(styles) == 1
    assert len(scripts) == 1
    assert styles[0].text.strip()
    assert scripts[0].text.strip()
    assert "src" not in scripts[0].attributes


def test_document_ends_with_exactly_one_trailing_newline(
    sample_document: str,
) -> None:
    """One line feed after the closing tag, as the reference artifact has.

    The engine strips the trailing newline by default, and a file that ends
    mid-line is a diagnostic from ordinary text tooling, so the writer restores
    it -- and restores exactly one.
    """
    assert sample_document.endswith("</html>\n")
    assert not sample_document.endswith("</html>\n\n")


def test_document_carries_no_merge_conflict_marker(sample_document: str) -> None:
    """None of the three markers is ever emitted.

    They exist in the unmodified reference checkout's committed artifacts, and
    the inlined stylesheet and script are scanned as part of this because they
    travel inside the page: a rule divider written as a run of equals signs in
    either asset would put a marker in every report.
    """
    for marker in CONFLICT_MARKERS:
        assert marker not in sample_document, f"{marker!r} reached the artifact"


def test_document_is_well_formed_markup(sample_dom: ParsedDocument) -> None:
    """No stray end tag, no crossed nesting, nothing left open.

    This is the malformed-page gate.  An inlined asset containing its own
    closing token would terminate its block early and turn the remainder of the
    page into text; the writer forbids that token, and this is the assertion
    that would notice if it ever got through.  The nesting half covers the other
    direction -- a template that lost an end tag -- which a balanced-looking
    tree would otherwise hide, because an enclosing end tag closes everything
    opened after it.
    """
    assert markup_faults(sample_dom) == []


# --------------------------------------------------------------------------- #
# Self-containment
# --------------------------------------------------------------------------- #


def test_sample_page_carries_no_external_reference(
    sample_dom: ParsedDocument,
) -> None:
    """Every reference on the page is inline, and the set of them is exact.

    The walk covers every URL-bearing attribute rather than ``href`` and
    ``src`` alone, and the positive half of the assertion matters as much as the
    negative one: the page must carry precisely two references -- the inline
    favicon and the failure screenshot the sample embeds -- so a change that
    dropped the screenshot would fail here too.
    """
    assert external_references(sample_dom) == []
    candidates = reference_candidates(sample_dom)
    assert len(candidates) == 2
    schemes = sorted(value.split(",")[0] for _tag, _name, value in candidates)
    assert schemes == [
        f"{DATA_URI_SCHEME}{PNG_MIME_TYPE};base64",
        FAVICON_DATA_PREFIX.rstrip(","),
    ]


def test_hostile_page_carries_no_external_reference(
    hostile_dom: ParsedDocument,
) -> None:
    """Hostile values do not become references.

    A value carrying markup, quotes and an engine expression is interpolated
    into a heading, an accessible name and an image caption; none of that may
    introduce an address.
    """
    assert external_references(hostile_dom) == []


def test_page_declares_no_absolute_or_protocol_relative_url(
    sample_document: str, sample_dom: ParsedDocument
) -> None:
    """No ``http://``, no ``https://``, and no ``//host`` reference.

    The text-level half holds even inside the inlined assets, because the
    favicon percent-encodes its own namespace URL and neither asset addresses
    anything; the attribute-level half catches the protocol-relative form, which
    a text scan for a scheme would miss entirely.
    """
    for scheme in ABSOLUTE_URL_SCHEMES:
        assert scheme not in sample_document, f"{scheme} reached the artifact"
    for tag, name, value in reference_candidates(sample_dom):
        assert not value.startswith(PROTOCOL_RELATIVE_PREFIX), f"{tag}[{name}]"


def test_inlined_stylesheet_fetches_nothing(sample_dom: ParsedDocument) -> None:
    """The inlined CSS declares no ``url(...)`` and no ``@import``.

    A font face, a background image or an imported sheet inside the stylesheet
    would be an external reference the attribute walk cannot see, because it
    lives in text rather than in markup.  The style block is asserted non-empty
    first, so this cannot pass by scanning nothing.
    """
    style_text = sample_dom.root.descendants("style")[0].text
    assert f".{BADGE_CLASS}" in style_text
    offenders = [
        target
        for target in stylesheet_references(style_text)
        if not is_self_contained(target)
    ]
    assert offenders == []


def test_write_html_report_produces_exactly_one_file(
    sample_result_set: Any, tmp_artifact_root: Path
) -> None:
    """The writer creates the build directory and the page, and nothing else.

    No stylesheet, script, image, font or sibling page is written beside it --
    that is the whole contract of this artifact -- so the census is taken over
    the entire root before and after, and the difference is named exactly.
    """
    assert file_census(tmp_artifact_root) == set()

    written = write_html_report(
        sample_result_set, base=tmp_artifact_root, generated_at=FIXED_GENERATED_AT
    )

    expected_html = paths.cucumber_reports_html_path(tmp_artifact_root)
    assert written == expected_html
    assert file_census(tmp_artifact_root) == {
        paths.target_root(tmp_artifact_root).relative_to(tmp_artifact_root).as_posix(),
        expected_html.relative_to(tmp_artifact_root).as_posix(),
    }


def test_written_file_is_utf8_with_line_feeds_and_matches_the_render(
    tmp_artifact_root: Path,
) -> None:
    """The bytes on disk are the rendered document, UTF-8, newline-only.

    The hostile document is the input because it is the one that proves the
    encoding: its values carry characters outside ASCII, which a mis-declared or
    platform-default encoding would mangle.  ``\\r\\n`` is asserted absent
    because a page written on Windows must be byte-identical to one written on
    Linux.  The file is then parsed where it lies, under the temporary artifact
    root, so the structural contract is asserted against the artifact a build
    would publish rather than against a string that never reached a disk.
    """
    document = hostile_result_document()
    written = write_html_report(
        document, base=tmp_artifact_root, generated_at=FIXED_GENERATED_AT
    )

    raw = written.read_bytes()
    assert b"\r\n" not in raw
    text = raw.decode("utf-8")
    assert text == render_html_report(document, generated_at=FIXED_GENERATED_AT)

    written_dom = parse_html(text)
    assert markup_faults(written_dom) == []
    assert written_dom.declarations == [DOCTYPE_DECLARATION]
    assert written_dom.root.descendants("title")[0].text == DOCUMENT_TITLE
    assert external_references(written_dom) == []
    assert HOSTILE_VALUE in written_dom.root.text


# --------------------------------------------------------------------------- #
# Publication -- how the one file reaches its destination
#
# THE CONTRACT MOVED, AND THESE TESTS ARE WHERE IT IS HELD.  The writer used to
# name its own temporary, open it with the builtin, sync it, rename it with
# ``os.replace`` and unlink it on failure -- all resolved from pathnames, and
# all after ``ensure_parent`` had verified the parent chain and *released* its
# descriptor.  The review's probe swapped ``target/`` in that window and the
# report was written outside the artifact root (CWE-59/CWE-367), so the whole
# sequence now belongs to :func:`app.utils.paths.publish_artifact_file`, which
# creates, writes, syncs and renames under one held directory descriptor.
#
# What that means for this file: nothing here may assert the writer's own
# temporary name -- there is no longer one -- and what is asserted instead is
# what an observer outside the writer can establish.  The temporary is
# dot-prefixed and unservable while it exists and gone afterwards, the
# destination is reached only by the rename, a hostile link or a hostile
# directory component is refused before any byte is written, and the artifact
# and its build directory are readable by their owner alone.
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not FILE_MODES_AVAILABLE,
    reason="this platform expresses permissions as ACLs, so the policy does not apply",
)
def test_the_published_page_and_its_build_directory_are_owner_only(
    sample_result_set: Any, tmp_artifact_root: Path
) -> None:
    """The page is ``0600`` under a build directory that grants nobody else.

    Measured output used to be ``0644`` under ``0755``, which exposed the run's
    evidence -- failure text and screenshots of a logged-in session -- to every
    local account (CWE-732/CWE-359).  The publication creates its temporary
    ``O_CREAT|O_EXCL`` at :data:`~app.utils.paths.ARTIFACT_FILE_MODE` and the
    rename carries that mode onto the destination, so publishing cannot loosen
    what writing in place would not.  The directory is asserted through the
    mask rather than against ``0700`` exactly, because a set-group-id checkout
    keeps that bit while granting the group nothing.
    """
    written = write_html_report(
        sample_result_set, base=tmp_artifact_root, generated_at=FIXED_GENERATED_AT
    )

    assert permission_mode(written) == paths.ARTIFACT_FILE_MODE
    build_directory = paths.target_root(tmp_artifact_root)
    assert permission_mode(build_directory) & paths.ARTIFACT_MODE_MASK == 0
    assert permission_mode(build_directory) & stat.S_IRWXU == stat.S_IRWXU


def test_the_publication_temporary_is_unservable_and_gone_afterwards(
    sample_result_set: Any,
    tmp_artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bytes land in a dot-prefixed temporary, and only a rename publishes them.

    This test replaces the assertion the old contract carried -- that the
    temporary matched this writer's ``_TEMPORARY_NAME`` -- because that
    constant is gone: the naming, the creation and the removal are the path
    authority's, and the writer no longer has a temporary to name.  What
    survives the move is the *observable* half, which is what mattered all
    along, so it is asserted from outside the writer: while the publication is
    open the build directory holds exactly one entry, it is dot-prefixed and
    suffixed, it carries the destination's name, and
    :func:`~app.utils.paths.resolve_artifact` refuses to serve it -- so
    ``GET /artifacts/<name>`` cannot reach a half-written page.  The
    destination does not exist yet at that moment, which is the proof it is
    reached only by the rename, and afterwards the temporary is gone.
    """
    observed: list[tuple[tuple[str, ...], tuple[Path | None, ...]]] = []

    @contextmanager
    def observing_publication(
        destination: Path, **options: Any
    ) -> Iterator[Any]:
        """Delegate to the real publication, snapshotting the directory inside it."""
        with paths.publish_artifact_file(destination, **options) as stream:
            entries = tuple(
                sorted(entry.name for entry in Path(destination).parent.iterdir())
            )
            observed.append(
                (
                    entries,
                    tuple(
                        paths.resolve_artifact(name, tmp_artifact_root)
                        for name in entries
                    ),
                )
            )
            yield stream

    monkeypatch.setattr(writer, "publish_artifact_file", observing_publication)

    written = write_html_report(
        sample_result_set, base=tmp_artifact_root, generated_at=FIXED_GENERATED_AT
    )

    assert len(observed) == 1
    entries, resolutions = observed[0]
    assert len(entries) == 1
    temporary_name = entries[0]
    assert temporary_name.startswith(TEMPORARY_PREFIX)
    assert temporary_name.endswith(TEMPORARY_SUFFIX)
    assert written.name in temporary_name
    assert written.name not in entries
    assert resolutions == (None,)
    assert file_census(tmp_artifact_root) == single_page_census(tmp_artifact_root)


def test_a_fault_while_the_page_is_written_keeps_the_previous_page(
    sample_result_set: Any,
    tmp_artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write that fails halfway leaves the last complete page and no scratch.

    The seam is the stream the publication yields, wrapped so that half the
    document reaches the real temporary and the write then fails the way a full
    filesystem fails.  Everything else is the production route: a real
    temporary under a real held descriptor, and the authority's own removal of
    it on the way out.  The census is asserted rather than the exception alone,
    because a cleanup that left the partial file behind would still raise.
    """
    first = write_html_report(
        sample_result_set, base=tmp_artifact_root, generated_at=FIXED_GENERATED_AT
    )
    complete_page = first.read_bytes()

    class HalfWritingStream:
        """A stream that writes half of what it is given and then fails."""

        def __init__(self, stream: Any) -> None:
            self._stream = stream

        def write(self, text: str) -> int:
            written_half = self._stream.write(text[: len(text) // 2])
            raise OSError(errno.ENOSPC, "No space left on device", written_half)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._stream, name)

    @contextmanager
    def half_writing_publication(
        destination: Path, **options: Any
    ) -> Iterator[Any]:
        """Delegate to the real publication, handing out the failing stream."""
        with paths.publish_artifact_file(destination, **options) as stream:
            yield HalfWritingStream(stream)

    monkeypatch.setattr(writer, "publish_artifact_file", half_writing_publication)

    with pytest.raises(OSError) as failure:
        write_html_report(
            sample_result_set, base=tmp_artifact_root, generated_at=FIXED_GENERATED_AT
        )
    assert failure.value.errno == errno.ENOSPC

    assert first.read_bytes() == complete_page
    assert file_census(tmp_artifact_root) == single_page_census(tmp_artifact_root)


def test_a_render_fault_reaches_no_file_at_all(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A template or asset fault happens before the publication opens anything.

    The ordering the writer promises, asserted at its strongest: with no
    previous page to fall back on, a failing render leaves the artifact root
    exactly as it was -- no build directory, no temporary, no empty file at the
    destination.
    """

    def exploding_render(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("the template could not be rendered")

    monkeypatch.setattr(writer, "render_html_report", exploding_render)

    with pytest.raises(RuntimeError, match="could not be rendered"):
        write_html_report(None, base=tmp_artifact_root)

    assert file_census(tmp_artifact_root) == set()


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be refused",
)
def test_a_build_directory_swapped_before_the_rename_cannot_receive_the_page(
    sample_result_set: Any,
    tmp_artifact_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The page lands in the directory that was verified, not in the name's.

    The other half of the finding, and the half a second pathname check cannot
    close: the swap is made *after* the document has been written and *before*
    the rename, which is the window a pathname-resolved ``os.replace`` leaves
    open -- it would deposit the report in whatever directory ``target/`` now
    answered to.  The rename is descriptor-relative, so the artifact reaches
    the directory the publication verified and holds the whole document, and
    the substituted directory receives nothing.
    """
    outside = tmp_path / OUTSIDE_DIR_NAME
    outside.mkdir()
    stashed = tmp_path / "stashed-build-directory"
    verified = paths.ensure_dir(paths.target_root(tmp_artifact_root))
    real_publication = paths.publish_artifact_file

    @contextmanager
    def publication_swapped_before_the_rename(
        destination: Path, **options: Any
    ) -> Iterator[Any]:
        """Delegate, and substitute the build directory once the write is done."""
        with real_publication(destination, **options) as stream:
            yield stream
            os.rename(verified, stashed)
            os.symlink(outside, verified, target_is_directory=True)

    monkeypatch.setattr(
        writer, "publish_artifact_file", publication_swapped_before_the_rename
    )

    write_html_report(
        sample_result_set, base=tmp_artifact_root, generated_at=FIXED_GENERATED_AT
    )

    assert file_census(outside) == set()
    published = stashed / paths.CUCUMBER_REPORTS_HTML_NAME
    assert file_census(stashed) == {published.name}
    assert published.read_text(encoding="utf-8") == render_html_report(
        sample_result_set, generated_at=FIXED_GENERATED_AT
    )


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be refused",
)
def test_a_symlinked_destination_is_refused_and_its_target_survives(
    sample_result_set: Any, tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """A link standing in for the page is refused, byte for byte intact outside.

    The publication would replace the link rather than follow it, but the entry
    *is* a file elsewhere: the artifact would go missing and the outside file
    would be the one the next reader found.  Refused before anything is
    written, with the outside file asserted unchanged and the build directory
    holding nothing but the refused link.
    """
    outside = tmp_path / OUTSIDE_FILE_NAME
    outside.write_text(OUTSIDE_FILE_CONTENT, encoding="utf-8")
    destination = paths.cucumber_reports_html_path(tmp_artifact_root)
    paths.ensure_dir(destination.parent)
    destination.symlink_to(outside)

    with pytest.raises(paths.ArtifactPathError) as failure:
        write_html_report(
            sample_result_set, base=tmp_artifact_root, generated_at=FIXED_GENERATED_AT
        )
    assert destination.name in str(failure.value)
    assert isinstance(failure.value, OSError)

    assert outside.read_text(encoding="utf-8") == OUTSIDE_FILE_CONTENT
    assert file_census(tmp_artifact_root) == single_page_census(tmp_artifact_root)
    assert destination.is_symlink()


@pytest.mark.skipif(
    not HARD_LINKS_AVAILABLE,
    reason="this platform cannot create hard links, so none can be refused",
)
def test_a_hard_linked_destination_is_refused_and_its_twin_survives(
    sample_result_set: Any, tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """A page hard-linked to a file outside the root is refused the same way.

    No symbolic link exists anywhere in this path, so no symlink check can see
    it: the entry *is* the outside file, and the link count is the only thing
    that can tell.  A rename over it would make the outside file disappear from
    its own directory's point of view.
    """
    outside = tmp_path / OUTSIDE_FILE_NAME
    outside.write_text(OUTSIDE_FILE_CONTENT, encoding="utf-8")
    destination = paths.cucumber_reports_html_path(tmp_artifact_root)
    paths.ensure_dir(destination.parent)
    os.link(outside, destination)

    with pytest.raises(paths.ArtifactPathError) as failure:
        write_html_report(
            sample_result_set, base=tmp_artifact_root, generated_at=FIXED_GENERATED_AT
        )
    assert "hard link" in str(failure.value)
    assert isinstance(failure.value, OSError)

    assert outside.read_text(encoding="utf-8") == OUTSIDE_FILE_CONTENT
    assert destination.read_text(encoding="utf-8") == OUTSIDE_FILE_CONTENT
    assert file_census(tmp_artifact_root) == single_page_census(tmp_artifact_root)


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be refused",
)
def test_a_symlinked_build_directory_is_refused_and_nothing_is_written_through_it(
    sample_result_set: Any, tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """A link standing in for ``target/`` is refused, and its destination stays empty.

    **This is the test that proves the finding closed.**  The review's probe
    used exactly this shape: with the parent verified and then released, the
    temporary was created and renamed through the pathname, and the report
    landed in whatever directory ``target/`` now answered to -- outside the
    artifact root.  The outside directory is therefore asserted EMPTY, because
    a refusal that has already created the temporary in the link's destination
    is not a refusal.
    """
    outside = tmp_path / OUTSIDE_DIR_NAME
    outside.mkdir()
    paths.target_root(tmp_artifact_root).symlink_to(outside, target_is_directory=True)

    with pytest.raises(paths.ArtifactPathError) as failure:
        write_html_report(
            sample_result_set, base=tmp_artifact_root, generated_at=FIXED_GENERATED_AT
        )
    assert paths.TARGET_DIR_NAME in str(failure.value)
    assert isinstance(failure.value, OSError)

    assert file_census(outside) == set()
    assert not paths.cucumber_reports_html_path(tmp_artifact_root).exists()


# --------------------------------------------------------------------------- #
# Content completeness -- AAP 0.3.4's acceptance criterion for this artifact
#
# THE ORACLE IS INDEPENDENT OF THE SUBJECT, DELIBERATELY.  Every expectation
# below comes from :func:`select_emitted_features` -- this module's own reading
# of the selection rule ``app/reporting/html_report.py`` documents -- and from
# the pinned counts and sentinels beside it, never from the writer's
# ``emitted_features``.  Deriving the expected records through the same function
# that drives rendering would make these assertions tautological: a selector
# that silently dropped a whole selected feature would drop it from the page
# and from the expectation together, and the comparison would still hold.  The
# pinned counts are what close that hole, and
# :func:`test_the_independent_oracle_agrees_with_the_writers_selection` is what
# keeps the two readings of the rule honest about each other.
# --------------------------------------------------------------------------- #


def test_the_independent_oracle_agrees_with_the_writers_selection(
    sample_result_set: Any,
) -> None:
    """This module's reading of the selection rule matches the writer's.

    Asserted in both directions and against fixed numbers.  The fixed numbers
    are the load-bearing half: they are what a regression in *either* reading
    cannot satisfy, and they are what makes the tests below able to fail when a
    whole selected feature disappears.  The two readings are then compared to
    each other so that a change to the documented rule is caught here, in one
    place, rather than surfacing as a puzzling content failure.
    """
    oracle = select_emitted_features(sample_result_set)
    subject = emitted_features(sample_result_set)

    assert tuple(feature["uri"] for feature in oracle) == SAMPLE_EMITTED_FEATURE_URIS
    assert (
        tuple(f"{feature['keyword']}: {feature['name']}" for feature in oracle)
        == SAMPLE_EMITTED_FEATURE_TITLES
    )
    assert oracle_element_count(oracle) == SAMPLE_EMITTED_ELEMENT_COUNT
    assert oracle_scenario_count(oracle) == SAMPLE_SELECTED_SCENARIO_COUNT
    assert oracle_step_count(oracle) == SAMPLE_EMITTED_STEP_COUNT
    assert tuple(feature["uri"] for feature in subject) == SAMPLE_EMITTED_FEATURE_URIS
    assert oracle_element_count(subject) == SAMPLE_EMITTED_ELEMENT_COUNT


def test_every_emitted_feature_is_present_in_the_dom(
    sample_dom: ParsedDocument,
) -> None:
    """One section per emitted feature, each headed keyword and name.

    The count is pinned and each feature's own unique sentinel is required, so
    a selection fault that dropped a whole feature fails here.  The sentinels
    are needed as well as the titles because two of this suite's features share
    a title -- Contact and Inventory are both "Testinium app Inventory feature"
    -- so a title list alone cannot tell which of them is on the page.
    """
    sections = sample_dom.root.with_class(FEATURE_CLASS)
    assert len(sections) == SAMPLE_EMITTED_FEATURE_COUNT

    headings = [section.descendants("h2")[0].normalized_text for section in sections]
    for heading, title in zip(headings, SAMPLE_EMITTED_FEATURE_TITLES, strict=True):
        assert heading.startswith(title)

    for uri, sentinel in SAMPLE_FEATURE_SENTINELS.items():
        owning = [
            section
            for section in sections
            if sentinel in section.normalized_text
        ]
        assert len(owning) == 1, f"{uri} is not present exactly once via {sentinel!r}"


def test_every_selected_scenario_and_background_is_present_in_the_dom(
    sample_result_set: Any, sample_dom: ParsedDocument
) -> None:
    """Every element the writer emits is rendered, in the model's own order.

    Background occurrences are included and are expected to repeat: the model
    records one per scenario, and the page shows each where the model puts it.
    The expected list comes from this module's own selection rule and its
    length is pinned, so the ordered comparison cannot be satisfied by an
    expectation that shrank along with the page.
    """
    expected = [
        f"{element['keyword']}: {element['name']}"
        for feature in select_emitted_features(sample_result_set)
        for element in feature["elements"]
    ]
    assert len(expected) == SAMPLE_EMITTED_ELEMENT_COUNT

    headings = [
        heading.normalized_text
        for section in sample_dom.root.with_class(FEATURE_CLASS)
        for heading in section.descendants("h3")
    ]
    assert len(headings) == SAMPLE_EMITTED_ELEMENT_COUNT
    for heading, title in zip(headings, expected, strict=True):
        assert heading.startswith(title)
    assert (
        len(sample_dom.root.with_class(SCENARIO_CLASS))
        == SAMPLE_SELECTED_SCENARIO_COUNT
    )


def test_every_step_keyword_name_and_status_is_present_in_the_dom(
    sample_result_set: Any, sample_dom: ParsedDocument
) -> None:
    """Every step reaches the page with its keyword, its name and its status.

    The comparison is a whole ordered list rather than a membership test, so a
    step rendered twice, a step dropped, or two steps swapped fails it -- which
    is what AAP 0.3.4 means by checking page structure rather than item counts.
    """
    expected = [
        (
            step["keyword"].strip(),
            step["name"],
            status_token(step["result"]["status"]).capitalize(),
        )
        for feature in select_emitted_features(sample_result_set)
        for element in feature["elements"]
        for step in element["steps"]
    ]
    assert len(expected) == SAMPLE_EMITTED_STEP_COUNT
    rendered = [
        (
            row.with_class(STEP_KEYWORD_CLASS)[0].text,
            row.with_class(STEP_NAME_CLASS)[0].text,
            first_badge_label(row),
        )
        for row in sample_dom.root.with_class(STEP_CLASS)
    ]
    assert rendered == expected


def test_every_scenario_start_timestamp_is_present_as_a_time_element(
    sample_result_set: Any, sample_dom: ParsedDocument
) -> None:
    """Each scenario's own start time is rendered inside that scenario.

    Scoped per scenario rather than asserted against the whole page, so a
    timestamp rendered under the wrong scenario is a failure rather than a pass.
    The machine-readable ``datetime`` attribute and the visible text are both
    checked, because a reader needs the one and a tool needs the other.
    """
    expected = [
        element.get("start_timestamp")
        for feature in select_emitted_features(sample_result_set)
        for element in feature["elements"]
        if element["type"] == ELEMENT_TYPE_SCENARIO
    ]
    assert len(expected) == SAMPLE_SELECTED_SCENARIO_COUNT
    assert all(expected), "the sample carries no scenario start times"

    scenarios = sample_dom.root.with_class(SCENARIO_CLASS)
    assert len(scenarios) == SAMPLE_SELECTED_SCENARIO_COUNT
    for scenario, started in zip(scenarios, expected, strict=True):
        stamps = scenario.descendants("time")
        assert [stamp.attributes.get("datetime") for stamp in stamps] == [started]
        assert stamps[0].text == started


def test_every_error_message_is_present_verbatim(
    sample_result_set: Any, sample_dom: ParsedDocument
) -> None:
    """Failure text reaches the page unaltered, in a preformatted block.

    Verbatim matters: the line endings and the leading indentation of a
    traceback are part of what a reader needs, and the sample's two messages
    carry both a Selenium message and a Python traceback.
    """
    expected = [
        step["result"]["error_message"]
        for feature in emitted_features(sample_result_set)
        for element in feature["elements"]
        for step in element["steps"]
        if step["result"].get("error_message")
    ]
    assert len(expected) == 2, "the sample no longer carries two failures"

    blocks = [block.text for block in sample_dom.root.descendants(ERROR_ELEMENT)]
    assert blocks == expected


def test_the_png_embedding_travels_inside_the_page(
    sample_result_set: Any, sample_dom: ParsedDocument
) -> None:
    """The failure screenshot is inline data, captioned with its own name.

    This is the one place the page carries binary content, and the assertion
    covers the whole chain: the trigger the script recognises, the payload as a
    base64 data URI, the caption a reader sees, and the accessible name of the
    image itself.  The image is reached through the trigger rather than probed
    for the hook, because the hook belongs on the activatable element -- see
    :data:`SCREENSHOT_ATTRIBUTE` -- and an assertion that looked for it on the
    ``<img>`` would report "no screenshot" for a page that carries one.
    """
    embeddings = [
        embedding
        for feature in emitted_features(sample_result_set)
        for element in feature["elements"]
        for hook in element.get("after", [])
        for embedding in hook.get("embeddings", [])
    ]
    assert len(embeddings) == 1, "the sample no longer carries one embedding"
    embedded = embeddings[0]

    triggers = screenshot_triggers(sample_dom.root)
    assert len(triggers) == 1
    assert triggers[0].attributes[SCREENSHOT_NAME_ATTRIBUTE] == embedded["name"]

    shots = screenshot_images(sample_dom.root)
    assert len(shots) == 1
    source = shots[0].attributes["src"]
    assert source == (
        f"{DATA_URI_SCHEME}{embedded['mime_type']};base64,{embedded['data']}"
    )
    assert shots[0].attributes.get("alt") == embedded["name"]
    captions = [
        caption.normalized_text
        for caption in sample_dom.root.descendants("figcaption")
    ]
    assert captions == [embedded["name"]]


def test_the_unselected_scenario_never_reaches_the_document(
    sample_result_set: Any, sample_document: str
) -> None:
    """A scenario the tag expression did not select is absent, with its tag.

    The rule is the JSON writer's: such a scenario never started, the JVM
    emitted no test case for it, and rendering it would put a scenario on this
    page that ``target/cucumber.json`` does not carry.  Both its name and the
    tag that excluded it are asserted absent, so a page that dropped the
    scenario but leaked its tag into the filter vocabulary still fails.
    """
    dropped = [
        element
        for feature in sample_result_set["features"]
        for element in feature["elements"]
        if element.get("selected") is False
    ]
    assert len(dropped) == 1, "the sample no longer carries an unselected scenario"
    excluded = dropped[0]

    assert excluded["name"] not in sample_document
    for tag in excluded["tags"]:
        assert tag["name"] not in sample_document
    assert excluded["name"] not in parse_html(sample_document).root.text


def test_a_background_recognised_only_by_its_keyword_is_treated_as_one() -> None:
    """A background carrying no ``type`` is still a background, and shares a fate.

    The type is the model's discriminator and the keyword answers for a
    hand-built document that carries none.  Two consequences are asserted
    because both are load-bearing: such an occurrence is not counted as a test
    case by the tally, and it is dropped together with the scenario it precedes
    when that scenario was not selected -- otherwise the page would show a
    background for a test case it does not show.
    """
    keyword_only_background = {
        "keyword": BACKGROUND_KEYWORD,
        "name": "a background declared by keyword alone",
        "steps": [make_step("a background step")],
    }
    with_scenario = make_result_set(
        make_feature(
            "Keyword background",
            [
                dict(keyword_only_background),
                make_element("a selected scenario", [make_step("a step")]),
            ],
        )
    )

    document = parse_html(
        render_html_report(with_scenario, generated_at=FIXED_GENERATED_AT)
    )
    backgrounds = document.root.with_class(BACKGROUND_CLASS)
    assert len(backgrounds) == 1
    assert keyword_only_background["name"] in (
        backgrounds[0].descendants("h3")[0].normalized_text
    )
    tally = build_summary(emitted_features(with_scenario))
    assert tally["scenarios"]["total"] == 1
    assert tally["steps"]["total"] == 2

    without_scenario = make_result_set(
        make_feature(
            "Dropped unit",
            [
                dict(keyword_only_background),
                make_element(
                    "an unselected scenario", [make_step("a step")], selected=False
                ),
            ],
        )
    )
    dropped = parse_html(
        render_html_report(without_scenario, generated_at=FIXED_GENERATED_AT)
    )
    assert dropped.root.with_class(BACKGROUND_CLASS) == []
    assert dropped.root.with_class(FEATURE_CLASS) == []


def test_a_malformed_start_timestamp_is_not_reported_as_the_runs_start() -> None:
    """An unparseable timestamp is shown where it belongs and nowhere else.

    The value the model carries is displayed verbatim on its own scenario --
    reformatting it would make the page disagree with the JSON artifact -- but
    it is dropped from the run-level figures, because a string that is not a
    timestamp cannot be reported as when the run began.
    """
    malformed_stamp = "not a timestamp"
    result_set = make_result_set(
        make_feature(
            "Unparseable start",
            [
                make_element(
                    "a scenario that started at nonsense",
                    [make_step("a step")],
                    start_timestamp=malformed_stamp,
                )
            ],
        )
    )

    assert earliest_start(emitted_features(result_set)) is None

    document = parse_html(
        render_html_report(result_set, generated_at=FIXED_GENERATED_AT)
    )
    scenario = document.root.with_class(SCENARIO_CLASS)[0]
    stamps = scenario.descendants("time")
    assert [stamp.attributes.get("datetime") for stamp in stamps] == [malformed_stamp]
    assert stamps[0].text == malformed_stamp

    page_text = document.root.normalized_text
    assert "Run started" not in page_text
    assert "Earliest scenario start" not in page_text
    assert f"Report generated {FIXED_GENERATED_AT}" in page_text


def test_run_metadata_and_the_tally_are_present(
    sample_result_set: Any, sample_dom: ParsedDocument
) -> None:
    """The environment descriptor and the run's counts are both on the page.

    The tally's figures are recomputed here from the emitted features by the
    same public helpers the writer uses, so the assertion is that the page
    states the run's real counts -- not that it states some number.
    """
    metadata = sample_result_set["metadata"]
    page_text = sample_dom.root.normalized_text
    assert f"Implementation {metadata['implementation']['name']}" in page_text
    assert metadata["implementation"]["version"] in page_text
    assert f"Runtime {metadata['runtime']['name']}" in page_text
    assert f"Operating system {metadata['os']['name']}" in page_text
    assert f"CPU {metadata['cpu']['name']}" in page_text
    assert f"Report generated {FIXED_GENERATED_AT}" in page_text
    assert f"Run started {sample_result_set['started_at']}" in page_text

    features = emitted_features(sample_result_set)
    scenarios = [
        element
        for feature in features
        for element in feature["elements"]
        if element["type"] == ELEMENT_TYPE_SCENARIO
    ]
    steps = [
        step
        for feature in features
        for element in feature["elements"]
        for step in element["steps"]
    ]
    assert f"{len(features)} Features" in page_text
    assert f"{len(scenarios)} Scenarios" in page_text
    assert f"{len(steps)} Steps" in page_text


# --------------------------------------------------------------------------- #
# The generation time, and the clock this writer does not have
#
# A run has one generation time, resolved once before the fan-out - by the
# collector, or by ``app/services/report_service.py`` for a document that
# carries none - and carried in the document every writer is handed.  This
# writer therefore states the value it was given and never reads a clock: while
# it did, an empty run's page carried the moment somebody rendered it while the
# report tree's Date cell, which has no clock behind it, stayed empty for that
# same document, and the page's value changed on every render.  An absent stamp
# now costs the page a row, which is the same "state fewer facts rather than
# invented ones" policy every other descriptor row follows.
# --------------------------------------------------------------------------- #


def test_metadata_over_a_document_with_no_generation_time_is_blank() -> None:
    """No argument and no document value yields ``""``, never a clock reading.

    Four shapes of stampless document, because the writer's reads are total and
    each arrives in practice: nothing at all, a document carrying features but
    no top-level stamp, one whose stamp is present but unusable --
    ``new_result_set()`` carries ``generated_at`` as ``None`` until the
    collector writes it, which is exactly the empty run this case exists for --
    and a document with nothing in it whatever.
    """
    for result_set in (None, {"features": []}, {"generated_at": None}, {}):
        metadata = build_metadata(result_set)
        assert metadata["generated_at"] == "", result_set
        assert metadata["started_at"] == "", result_set

    # The explicit argument still wins, and the document's own value still
    # stands in for it: what was removed is the clock behind them, not the
    # order of preference.
    assert (
        build_metadata(None, generated_at=FIXED_GENERATED_AT)["generated_at"]
        == FIXED_GENERATED_AT
    )
    assert (
        build_metadata({"generated_at": FIXED_GENERATED_AT})["generated_at"]
        == FIXED_GENERATED_AT
    )


def test_a_document_with_no_generation_time_renders_no_generated_row() -> None:
    """The row is omitted rather than filled with the render-time clock.

    ``app/templates/artifact/metadata.html`` filters its descriptor rows on a
    non-empty value, so a blank stamp costs the page that one row and nothing
    else: the document still renders whole, with its title, its doctype and the
    probed environment rows, and nothing surfaces as the characters ``None``.
    """
    markup = render_html_report(None)
    document = parse_html(markup)

    assert markup_faults(document) == []
    assert document.declarations == [DOCTYPE_DECLARATION]
    assert document.root.descendants("title")[0].text == DOCUMENT_TITLE

    rows = [row.normalized_text for row in document.root.with_class(META_ITEM_CLASS)]
    assert rows, "the probed environment rows should still be there"
    assert [row for row in rows if row.startswith(GENERATED_LABEL)] == [], rows
    page_text = document.root.normalized_text
    assert GENERATED_LABEL not in page_text
    assert "None" not in document.root.text
    assert TIMESTAMP_PATTERN.search(markup) is None, "the page carries a timestamp"


def test_the_writer_holds_no_clock_of_its_own(repo_root: Path) -> None:
    """Asserted over the module's source: no clock is read anywhere in it.

    The strongest form the contract can take, and the reason it is read with
    :mod:`ast` rather than with a regular expression: the module's prose
    legitimately discusses generation times and the timestamp format, so only
    the *code* can say whether a clock is reached.  Both halves are checked --
    the modules that provide one are not imported, and no call that would read
    one is made -- so a clock cannot come back as ``datetime.now(UTC)``, as a
    bare ``time()`` over a module-level alias, or through an import this test
    did not anticipate.
    """
    source = repo_root / "app" / "reporting" / "html_report.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    for module in CLOCK_MODULES:
        assert module not in imported, f"{module} is imported by the writer"

    called: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.append(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called.append(node.func.attr)
    offending = sorted(set(called) & CLOCK_CALL_NAMES)
    assert offending == [], f"the writer reads a clock: {offending}"


# --------------------------------------------------------------------------- #
# Status presentation
# --------------------------------------------------------------------------- #


def test_every_known_status_and_the_unknown_fallback_render_as_badges(
    every_status_document: str,
) -> None:
    """All seven tokens the model produces, plus the fallback, are presented.

    The eighth scenario carries a status the result model never produces.  It
    has to render as ``unknown`` rather than as a pass: a page must not claim a
    status it never read.
    """
    labels = set(badge_labels(parse_html(every_status_document).root))
    for status in KNOWN_STATUSES:
        assert status.capitalize() in labels, f"{status} is not presented"
    assert UNKNOWN_STATUS.capitalize() in labels
    assert status_token(GARBAGE_STATUS) == UNKNOWN_STATUS


def test_status_hook_and_badge_agree_on_every_container(
    sample_dom: ParsedDocument, every_status_document: str
) -> None:
    """Each container's status attribute matches the badge shown on it.

    The attribute is read through :data:`STATUS_HOOK_ATTRIBUTES` because the
    template and static-asset units are consolidating the two spellings the
    project currently emits; pinning one here would turn this test red the
    moment that change merges, while the badge's text content -- its accessible
    name -- is stable either way.
    """
    for document in (sample_dom, parse_html(every_status_document)):
        containers = status_containers(document)
        assert containers, "the page presented no status-bearing container"
        for container in containers:
            token = container.status_hook
            assert token is not None, f"{container!r} carries no status hook"
            assert first_badge_label(container) == token.capitalize()


def test_one_failed_step_makes_its_feature_present_as_failed() -> None:
    """Roll-up precedence is visible on the page, not averaged away.

    ``failed`` outranks everything in :data:`STATUS_PRECEDENCE`, so a feature
    holding one failing step is presented failed however many passes surround
    it -- while the passing scenario beside it stays passed, because a scenario
    is not coloured by its neighbours.
    """
    passing = make_element("all good", [make_step("a passing step", status="passed")])
    failing = make_element(
        "one bad step",
        [
            make_step("a passing step", status="passed"),
            make_step("a failing step", status="failed"),
            make_step("a skipped step", status="skipped"),
        ],
    )
    document = parse_html(
        render_html_report(
            make_result_set(make_feature("Precedence", [passing, failing])),
            generated_at=FIXED_GENERATED_AT,
        )
    )

    assert STATUS_PRECEDENCE[0] == "failed"
    assert feature_status(make_feature("Precedence", [passing, failing])) == "failed"
    section = document.root.with_class(FEATURE_CLASS)[0]
    assert first_badge_label(section) == "Failed"
    scenarios = section.with_class(SCENARIO_CLASS)
    assert [first_badge_label(scenario) for scenario in scenarios] == [
        "Passed",
        "Failed",
    ]


def test_element_with_no_steps_presents_the_empty_element_status() -> None:
    """A step-less element takes :data:`EMPTY_ELEMENT_STATUS`.

    Measured rather than chosen: ``EmployeeFc.feature`` declares a background
    with an empty body and the reference generator renders each of its
    occurrences as passed, so the port presents it the same way.
    """
    document = parse_html(
        render_html_report(
            make_result_set(
                make_feature("Stepless", [make_element("nothing ran here", [])])
            ),
            generated_at=FIXED_GENERATED_AT,
        )
    )

    scenarios = document.root.with_class(SCENARIO_CLASS)
    assert len(scenarios) == 1
    assert first_badge_label(scenarios[0]) == EMPTY_ELEMENT_STATUS.capitalize()
    assert scenarios[0].status_hook == EMPTY_ELEMENT_STATUS


def test_feature_with_no_elements_presents_the_empty_aggregate_status() -> None:
    """A feature carrying nothing takes :data:`EMPTY_AGGREGATE_STATUS`.

    Rendered through the template directly, with a context built from the
    writer's own public helpers, because the selection rule drops such a feature
    before it can reach a page: a feature left with no test case is one the JVM
    would have emitted no feature map for.  This is the presentation half of the
    contract -- a feature with nothing under it did not pass, it did not run --
    and the empty state that stands in for its elements.
    """
    bare = make_feature("Nothing under it", [])
    assert feature_status(bare) == EMPTY_AGGREGATE_STATUS
    features = decorated_features([bare])
    assert [feature["status"] for feature in features] == [EMPTY_AGGREGATE_STATUS]

    context = build_render_context(None, generated_at=FIXED_GENERATED_AT)
    context["features"] = features
    markup = build_environment().get_template(REPORT_TEMPLATE).render(**context)
    document = parse_html(markup)

    assert markup_faults(document) == []
    sections = document.root.with_class(FEATURE_CLASS)
    assert len(sections) == 1
    assert sections[0].status_hook == EMPTY_AGGREGATE_STATUS
    assert first_badge_label(sections[0]) == EMPTY_AGGREGATE_STATUS.capitalize()
    assert sections[0].with_class(EMPTY_CLASS), "no empty state for the bare feature"


def test_a_status_the_model_never_produced_renders_rather_than_raising() -> None:
    """Garbage statuses of every shape present as ``unknown``.

    A number, ``None``, a container, a word the model does not use and a missing
    result are all put on one page.  A test outcome never raises, and a report
    is what a reader turns to when a run has gone wrong, so a malformed status
    has to render rather than take the page down.
    """
    elements = [
        make_element("numeric status", [make_step("a step", status=12345)]),
        make_element("null status", [make_step("a step", status=None)]),
        make_element("container status", [make_step("a step", status=["failed"])]),
        make_element("unproduced status", [make_step("a step", status=GARBAGE_STATUS)]),
        make_element("resultless step", [{"keyword": "Given ", "name": "a step"}]),
    ]
    document = parse_html(
        render_html_report(
            make_result_set(make_feature("Garbage statuses", elements)),
            generated_at=FIXED_GENERATED_AT,
        )
    )

    scenarios = document.root.with_class(SCENARIO_CLASS)
    assert len(scenarios) == len(elements)
    expected = UNKNOWN_STATUS.capitalize()
    for scenario in scenarios:
        assert first_badge_label(scenario) == expected
    assert document.declarations == [DOCTYPE_DECLARATION]


# --------------------------------------------------------------------------- #
# Escaping
# --------------------------------------------------------------------------- #


def test_hostile_values_are_escaped_and_never_executed(
    hostile_document: str,
) -> None:
    """Injected markup is escaped, and the engine expression is not evaluated.

    Both halves are needed.  The escaped form proves autoescaping ran; the
    absence of the value with its expression evaluated proves the result set is
    data rather than template source -- a page that rendered a scenario name as
    a template would let a feature file execute arbitrary expressions in the
    reporting process.
    """
    assert HOSTILE_MARKUP not in hostile_document
    assert ESCAPED_HOSTILE_MARKUP in hostile_document
    assert HOSTILE_EXPRESSION in hostile_document
    assert HOSTILE_VALUE_EVALUATED not in hostile_document


def test_hostile_values_survive_intact_in_every_interpolation_point(
    hostile_dom: ParsedDocument,
) -> None:
    """Escaping is reversible: each value reads back exactly as it went in.

    Escaped is not the same as mangled.  Every place the hostile value is
    interpolated is checked separately -- the feature heading, the scenario
    heading, the step name, both tag lists, the matched argument and the error
    block -- because a value escaped in one place and dropped in another would
    pass a document-wide membership test.
    """
    section = hostile_dom.root.with_class(FEATURE_CLASS)[0]
    assert HOSTILE_VALUE in section.descendants("h2")[0].text

    scenario = section.with_class(SCENARIO_CLASS)[0]
    assert HOSTILE_VALUE in scenario.descendants("h3")[0].text

    step = scenario.with_class(STEP_CLASS)[0]
    assert step.with_class(STEP_NAME_CLASS)[0].text == HOSTILE_VALUE
    assert step.with_class(STEP_KEYWORD_CLASS)[0].text == HOSTILE_VALUE
    assert HOSTILE_VALUE in step.with_class(STEP_ARGS_CLASS)[0].text
    assert step.descendants(ERROR_ELEMENT)[0].text == HOSTILE_VALUE

    tags = [tag.normalized_text for tag in hostile_dom.root.with_class(TAG_CLASS)]
    assert tags == [HOSTILE_TAG, HOSTILE_TAG]


def test_hostile_values_in_attributes_keep_the_document_parseable(
    hostile_dom: ParsedDocument,
) -> None:
    """A value full of quotes and angle brackets does not break an attribute.

    Result text becomes the accessible name of three controls -- the scenario
    toggle, the step toggle and the screenshot trigger -- so the hostile value
    lands inside an attribute as well as in text.  Unescaped, its double quote
    would end the attribute early and the rest of the value would be parsed as
    further attributes, which is why the parse is asserted clean and each
    attribute compared to the original.

    The three are enumerated rather than counted: a control that stopped
    carrying the value would otherwise be indistinguishable from one that was
    never there, and the screenshot trigger is exactly such a case -- it
    acquired its accessible name with the shared-partials migration.  Every
    label carrying the value is also required to carry it *whole*, because a
    value truncated at the quote is the failure mode this test exists for and
    a substring test alone would not see it.
    """
    assert markup_faults(hostile_dom) == []

    labels = [
        button.attributes["aria-label"]
        for button in hostile_dom.root.descendants("button")
        if "aria-label" in button.attributes
    ]
    carrying = [label for label in labels if HOSTILE_VALUE in label]
    assert len(carrying) == 3, (
        "the hostile value did not reach every accessible name: "
        f"{labels}"
    )
    for label in carrying:
        assert label.endswith(HOSTILE_VALUE), label

    triggers = screenshot_triggers(hostile_dom.root)
    assert len(triggers) == 1
    assert triggers[0].attributes[SCREENSHOT_NAME_ATTRIBUTE] == HOSTILE_VALUE
    assert HOSTILE_VALUE in triggers[0].attributes["aria-label"]

    images = screenshot_images(hostile_dom.root)
    assert len(images) == 1
    assert images[0].attributes["alt"] == HOSTILE_VALUE


# --------------------------------------------------------------------------- #
# Template loading
# --------------------------------------------------------------------------- #


def test_build_environment_autoescapes_and_keeps_the_trailing_newline() -> None:
    """The two settings every escaping and shape assertion above rests on.

    Autoescaping is asserted because result data carries tracebacks, quotes and
    braces and nothing in the templates marks a value trusted; the trailing
    newline because the reference artifact ends with one after its closing tag.
    Block trimming is asserted off: the artifact templates manage their own
    whitespace with explicit markers, and turning it on would change the emitted
    source of a document people read and diff.
    """
    environment = build_environment()

    assert environment.autoescape is True
    assert environment.keep_trailing_newline is True
    assert environment.trim_blocks is False
    assert environment.lstrip_blocks is False


def test_build_environment_resolves_templates_against_the_template_root() -> None:
    """The loader root is ``app.utils.paths.templates_dir`` and nothing else.

    That root is what makes the templates' own ``artifact/element.html`` and
    ``partials/status_badge.html`` references resolve, and it is
    package-relative, so the page renders identically from a source tree and
    from an installed wheel.
    """
    environment = build_environment()
    loader = environment.loader

    assert isinstance(loader, FileSystemLoader)
    assert [Path(entry) for entry in loader.searchpath] == [paths.templates_dir()]
    assert REPORT_TEMPLATE in set(environment.list_templates())
    assert environment.get_template(REPORT_TEMPLATE) is not None


def test_render_uses_a_caller_supplied_environment(sample_result_set: Any) -> None:
    """A supplied environment is the one that renders, and the shape still holds.

    Proved by loading through a recording loader: the templates the render
    actually pulled are observable, and they are the root document plus the
    three artifact blocks and the three shared partials -- the whole template
    dependency set of this artifact.  The environment deliberately does *not*
    keep the trailing newline, which is the second half of the assertion: the
    writer restores it itself, so a caller's environment cannot leave the file
    ending mid-line.
    """
    loaded: list[str] = []

    class RecordingLoader(FileSystemLoader):
        """A loader that notes every template name it is asked for."""

        def get_source(
            self, environment: Environment, template: str
        ) -> tuple[str, str | None, Any]:
            """Record the request, then load it exactly as the base class does."""
            loaded.append(template)
            return super().get_source(environment, template)

    environment = Environment(
        loader=RecordingLoader(str(paths.templates_dir())),
        autoescape=True,
        keep_trailing_newline=False,
    )

    rendered = render_html_report(
        sample_result_set, environment=environment, generated_at=FIXED_GENERATED_AT
    )

    assert set(loaded) == {
        REPORT_TEMPLATE,
        "artifact/metadata.html",
        "artifact/feature.html",
        "artifact/element.html",
        "partials/status_badge.html",
        "partials/step_row.html",
        "partials/lightbox.html",
    }
    assert rendered.endswith("</html>\n")
    assert not rendered.endswith("</html>\n\n")
    assert rendered == render_html_report(
        sample_result_set, generated_at=FIXED_GENERATED_AT
    )


# --------------------------------------------------------------------------- #
# The two inlined assets
# --------------------------------------------------------------------------- #


def test_the_shared_partials_emit_no_reference_this_artifact_could_not_carry() -> None:
    """Each shared partial is self-contained on its own, not only in context.

    The three partials under ``partials/`` are rendered into this artifact, into
    the report tree and into the framework views, and those three surfaces do
    not agree about assets: this page forbids every reference that is not an
    inline payload, while a view resolves its stylesheet through the framework.
    A partial that acquired an address would therefore be correct on one
    surface and broken on this one.

    The whole-page checks above can only catch such a regression where the
    sample happens to render the offending branch; this one renders every macro
    of every partial directly, across the parameter space each one exposes --
    including an attachment with no payload and an overlay with no label -- and
    holds the output to the artifact's own rule.  A macro that grew a
    placeholder image, an icon sprite or an endpoint URL fails here whatever
    the sample carries.
    """
    environment = build_environment()
    badges = environment.get_template("partials/status_badge.html").module
    rows = environment.get_template("partials/step_row.html").module
    lightbox = environment.get_template("partials/lightbox.html").module

    step = make_step("a step", status="failed", error_message="boom", arguments=[])
    embedding = make_embedding(name="a screenshot")
    fragments: list[str] = [
        str(badges.status_badge(status))  # type: ignore[attr-defined]
        for status in (*KNOWN_STATUSES, UNKNOWN_STATUS, None, "")
    ]
    fragments.append(str(badges.status_badge("passed", label="Custom", title="Tip")))  # type: ignore[attr-defined]
    fragments.append(str(rows.step_row(step)))  # type: ignore[attr-defined]
    fragments.append(
        str(rows.step_row(step, uid="u1", show_location=True, inline_args=True))  # type: ignore[attr-defined]
    )
    fragments.append(str(rows.step_row(step, embeddings=[embedding])))  # type: ignore[attr-defined]
    fragments.append(str(lightbox.screenshot(embedding, caption="A caption")))  # type: ignore[attr-defined]
    fragments.append(str(lightbox.screenshots([embedding, make_embedding(data=None)])))  # type: ignore[attr-defined]
    fragments.append(str(lightbox.screenshots([])))  # type: ignore[attr-defined]
    fragments.append(str(lightbox.lightbox_overlay()))  # type: ignore[attr-defined]
    fragments.append(str(lightbox.lightbox_overlay(label="Screenshot")))  # type: ignore[attr-defined]

    # Eleven badges (the seven known statuses, the fallback, none, blank, and
    # one with a caller-supplied label and title), three step rows and five
    # lightbox fragments.
    assert len(fragments) == 19
    for fragment in fragments:
        document = parse_html(fragment)
        assert external_references(document) == [], fragment
        for scheme in ABSOLUTE_URL_SCHEMES:
            assert scheme not in fragment, fragment
        assert PROTOCOL_RELATIVE_PREFIX not in fragment, fragment
    # The positive half: the two partials that are given an attachment do emit
    # its inline payload, so "no reference" is not satisfied by emitting
    # nothing at all.
    assert any(
        f"{DATA_URI_SCHEME}{PNG_MIME_TYPE};base64" in fragment
        for fragment in fragments
    )


def test_inline_asset_returns_trusted_markup_for_a_first_party_asset(
    tmp_path: Path,
) -> None:
    """The asset is read as UTF-8 and handed back as markup, verbatim.

    Markup is the point: these two values are the only ones in the project
    declared trusted, and they are declared here, in Python, rather than in a
    template -- which is what keeps autoescaping meaningful for every value the
    templates interpolate.
    """
    asset = tmp_path / "asset.css"
    asset.write_text(f".rule {{ content: '{HOSTILE_VALUE}'; }}\n", encoding="utf-8")

    inlined = inline_asset(asset)

    assert isinstance(inlined, Markup)
    assert inlined == asset.read_text(encoding="utf-8")


@pytest.mark.parametrize("content", ["", "   \n\t\n"])
def test_inline_asset_rejects_an_empty_or_blank_asset(
    tmp_path: Path, content: str
) -> None:
    """An asset with no content is a packaging fault, not a styling choice.

    An empty style block would yield a plausible-looking page with no styling at
    all, which is worse than a loud failure at write time.
    """
    asset = tmp_path / "empty.css"
    asset.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        inline_asset(asset)


@pytest.mark.parametrize(
    ("forbidden", "content"),
    [
        (FORBIDDEN_IN_STYLE, ".rule { content: '</style>'; }\n"),
        (FORBIDDEN_IN_STYLE, ".rule { content: '</STYLE >'; }\n"),
        (FORBIDDEN_IN_SCRIPT, "var marker = '</script>';\n"),
        (FORBIDDEN_IN_SCRIPT, "var marker = '</SCRIPT>';\n"),
    ],
)
def test_inline_asset_rejects_an_asset_that_would_close_its_block(
    tmp_path: Path, forbidden: str, content: str
) -> None:
    """The closing token is refused, and refused case-insensitively.

    A script block is closed by that token wherever it occurs -- inside a string
    literal or a comment included -- and the remainder of the asset would be
    rendered as document text, corrupting everything after it.  The parser is
    case-insensitive, so the check is too.
    """
    asset = tmp_path / "asset.txt"
    asset.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="terminate its block early"):
        inline_asset(asset, forbidden)


def test_the_real_assets_resolve_under_the_static_root() -> None:
    """Both inlined assets are package-relative files that exist.

    Package-relative rather than working-directory-relative is what makes the
    writer correct in a worker process started anywhere, and what lets the
    assets travel inside an installed wheel.
    """
    static_root = paths.static_dir()

    assert css_asset_path() == static_root.joinpath(*CSS_ASSET_PARTS)
    assert js_asset_path() == static_root.joinpath(*JS_ASSET_PARTS)
    assert css_asset_path().is_file()
    assert js_asset_path().is_file()


def test_the_asset_text_is_inlined_into_the_pages_own_blocks(
    sample_dom: ParsedDocument,
) -> None:
    """The stylesheet and the script arrive whole, each inside its own block.

    Whole, not merely non-empty: a truncated asset is the failure mode a
    length-free assertion would miss, and the block a fragment landed in is what
    decides whether the page styles itself or runs.
    """
    css_text = css_asset_path().read_text(encoding="utf-8")
    js_text = js_asset_path().read_text(encoding="utf-8")

    assert css_text in sample_dom.root.descendants("style")[0].text
    assert js_text in sample_dom.root.descendants("script")[0].text


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #


#: The six routes to "nothing to report", built once at import time.  Sharing
#: one document across the six cases is safe by contract: the writer selects,
#: copies and tallies without ever mutating the result set it was handed -- it
#: is one of four writers fed from a single merged document, and a writer that
#: edited that document in place would change what the others see.
EMPTY_INPUT_CASES: Final[tuple[tuple[str, Any], ...]] = (
    ("nothing at all", None),
    ("an empty document", {}),
    ("no features", {"features": []}),
    ("a feature with no elements", make_result_set(make_feature("Bare", []))),
    (
        "a feature whose only scenario was not selected",
        make_result_set(
            make_feature(
                "All filtered out",
                [
                    make_element(
                        "not selected",
                        [make_step("a step")],
                        selected=False,
                    )
                ],
            )
        ),
    ),
    (
        "a feature left with a background and no test case",
        make_result_set(
            make_feature(
                "Background only",
                [
                    make_element(
                        "a background with no scenario after it",
                        [make_step("a step")],
                        element_type=ELEMENT_TYPE_BACKGROUND,
                        keyword=BACKGROUND_KEYWORD,
                    )
                ],
            )
        ),
    ),
)


@pytest.mark.parametrize(
    ("label", "result_set"),
    EMPTY_INPUT_CASES,
    ids=[label for label, _payload in EMPTY_INPUT_CASES],
)
def test_an_empty_run_still_renders_a_complete_page(
    label: str, result_set: Any
) -> None:
    """Every shape of "nothing to report" yields a whole, valid page.

    The command's exit contract writes all four artifacts even when the tag
    expression selected nothing, so a run that matched no scenario is a
    legitimate outcome rather than an error -- and the page has to say so
    instead of rendering a shell.  All six shapes are covered because each
    reaches the empty state by a different route: no document, no feature list,
    an empty one, a feature with nothing under it, a feature whose scenarios
    were all filtered out, and a feature left holding only a background.
    """
    markup = render_html_report(result_set, generated_at=FIXED_GENERATED_AT)
    document = parse_html(markup)

    assert document.declarations == [DOCTYPE_DECLARATION]
    assert markup_faults(document) == []
    assert document.root.descendants("title")[0].text == DOCUMENT_TITLE
    assert document.root.descendants("style")[0].text.strip()
    assert document.root.descendants("script")[0].text.strip()
    assert markup.endswith("</html>\n")
    assert external_references(document) == []

    assert document.root.with_class(FEATURE_CLASS) == [], label
    empty_states = [state.normalized_text for state in document.root.with_class(EMPTY_CLASS)]
    assert len(empty_states) == 2, label
    assert all(state for state in empty_states)


def test_the_zero_feature_page_offers_no_filter_it_cannot_apply(
    every_status_document: str,
) -> None:
    """Filter controls appear only when there is something to filter.

    Asserted against both ends of the contract in one test, because the
    interesting half is the comparison: a populated page carries one control per
    status actually present plus the "all results" control, and the empty page
    carries none at all -- a filter button that could only ever blank the page is
    worse than no button.
    """
    populated = parse_html(every_status_document)
    populated_filters = [
        element.attributes[FILTER_ATTRIBUTE]
        for element in populated.root.walk()
        if FILTER_ATTRIBUTE in element.attributes
    ]
    assert "all" in populated_filters
    for status in (*KNOWN_STATUSES, UNKNOWN_STATUS):
        assert status in populated_filters

    empty = parse_html(render_html_report(None, generated_at=FIXED_GENERATED_AT))
    assert [
        element
        for element in empty.root.walk()
        if FILTER_ATTRIBUTE in element.attributes
    ] == []


def test_a_malformed_embedding_emits_no_broken_image() -> None:
    """An unusable attachment is dropped, never rendered as a broken source.

    Four ways an attachment arrives unusable are covered: no payload key at all,
    a blank payload, a media type that is not an image, and list entries that
    are not attachments.  None may raise, and none may produce an ``img`` whose
    source is an empty or truncated data URI -- the page would show a broken
    image where a reader expects evidence.
    """
    elements = [
        make_element(
            "missing payload",
            [make_step("a step", status="failed")],
            after=[{"embeddings": [make_embedding(data=None, name="no payload")]}],
        ),
        make_element(
            "blank payload",
            [make_step("a step", status="failed")],
            after=[{"embeddings": [make_embedding(data="   ", name="blank")]}],
        ),
        make_element(
            "not an image",
            [make_step("a step", status="failed")],
            after=[
                {
                    "embeddings": [
                        make_embedding(mime_type=NON_IMAGE_MIME_TYPE, name="text")
                    ]
                }
            ],
        ),
        make_element(
            "junk entries",
            [make_step("a step", status="failed")],
            embeddings=["not an attachment", None, 7],
        ),
    ]
    document = parse_html(
        render_html_report(
            make_result_set(make_feature("Unusable attachments", elements)),
            generated_at=FIXED_GENERATED_AT,
        )
    )

    assert document.root.descendants("figure") == []
    # Read through the trigger, like every other screenshot assertion here: a
    # probe for the hook on the ``<img>`` would be empty whether or not a
    # screenshot had been emitted, so it would stop proving anything.
    assert screenshot_triggers(document.root) == []
    assert screenshot_images(document.root) == []

    images = document.root.descendants("img")
    assert len(images) == 1, "the page chrome should carry one lightbox image"
    assert LIGHTBOX_IMAGE_ATTRIBUTE in images[0].attributes
    assert "src" not in images[0].attributes
    assert external_references(document) == []
    assert document.root.with_class(SCENARIO_CLASS) != []


def test_a_failing_metadata_probe_is_logged_and_does_not_fail_the_page(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A probe that raises costs the page a row, never the page itself.

    Describing the run's environment is best-effort: a report is not worth a
    failed suite, so an unavailable probe surfaces as an omitted row.  Both
    halves are asserted together -- the page still renders and states the one
    value it was given, and the failure is diagnosed on the log rather than
    swallowed, which is what makes the omission debuggable.  Nothing may surface
    as the characters ``None``, which is what an unguarded gap would look like.
    """

    def exploding_probe() -> dict[str, Any]:
        raise RuntimeError("the metadata probe is unavailable")

    monkeypatch.setattr(writer, "run_metadata", exploding_probe)

    with caplog.at_level("WARNING", logger=writer.logger.name):
        markup = render_html_report(None, generated_at=FIXED_GENERATED_AT)

    assert "Run metadata could not be probed" in caplog.text
    assert "the metadata probe is unavailable" in caplog.text

    document = parse_html(markup)
    assert markup_faults(document) == []
    assert document.declarations == [DOCTYPE_DECLARATION]
    assert document.root.descendants("title")[0].text == DOCUMENT_TITLE
    rows = [row.normalized_text for row in document.root.with_class("tqa-meta-item")]
    assert rows == [f"Report generated {FIXED_GENERATED_AT}"]
    assert "None" not in document.root.text


def test_a_structurally_malformed_result_set_still_renders_a_page() -> None:
    """Every read of the result set is total, so a broken document renders.

    A merge that produced nonsense -- features that are not mappings, elements
    that are a string, steps that are a mapping, a result that is not a mapping,
    tags and attachments of the wrong type -- is a document to render as well as
    it can be rendered, because this artifact is what a reader turns to when
    something has already gone wrong.  What survives is asserted too, so
    "renders" cannot mean "renders an empty page".
    """
    malformed: dict[str, Any] = {
        "metadata": "not a mapping",
        "started_at": 12,
        "features": [
            "not a feature",
            None,
            7,
            {"keyword": FEATURE_KEYWORD, "name": "unusable", "elements": "not a list"},
            {
                "keyword": FEATURE_KEYWORD,
                "name": "partly usable",
                "tags": "not a list",
                "line": True,
                "id": 5,
                "uri": None,
                "elements": [
                    "not an element",
                    {
                        "type": ELEMENT_TYPE_SCENARIO,
                        "keyword": SCENARIO_KEYWORD,
                        "name": "survivor",
                        "steps": {"not": "a list"},
                        "tags": [{"name": None}, "bare tag", 12],
                        "start_timestamp": 17,
                        "after": {"embeddings": []},
                        "embeddings": {"mime_type": PNG_MIME_TYPE},
                    },
                ],
            },
        ],
    }

    document = parse_html(
        render_html_report(malformed, generated_at=FIXED_GENERATED_AT)
    )

    assert document.declarations == [DOCTYPE_DECLARATION]
    assert markup_faults(document) == []
    assert external_references(document) == []

    sections = document.root.with_class(FEATURE_CLASS)
    assert len(sections) == 1
    assert "partly usable" in sections[0].descendants("h2")[0].normalized_text
    scenarios = sections[0].with_class(SCENARIO_CLASS)
    assert len(scenarios) == 1
    assert "survivor" in scenarios[0].descendants("h3")[0].normalized_text
    assert [tag.normalized_text for tag in scenarios[0].with_class(TAG_CLASS)] == [
        "bare tag"
    ]


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_two_renders_of_one_document_are_identical(sample_result_set: Any) -> None:
    """With the generation time pinned, one input yields one page, twice.

    Byte stability is otherwise impossible -- the page surfaces per-scenario
    timestamps, durations and a generation time -- so what is asserted is what
    AAP 0.6 calls structural determinism: pin the one value that legitimately
    varies and the rest of the document, every derived collection included, is
    built in a fixed order.
    """
    first = render_html_report(sample_result_set, generated_at=FIXED_GENERATED_AT)
    second = render_html_report(sample_result_set, generated_at=FIXED_GENERATED_AT)

    assert first == second


def test_two_writes_of_one_document_leave_identical_files(
    sample_result_set: Any, tmp_artifact_root: Path
) -> None:
    """Writing twice overwrites in place and adds nothing to the tree.

    The writer deletes nothing and creates nothing but its own file, so a second
    write must leave the same single artifact -- not a second copy, a backup or
    a partial file beside it.
    """
    first = write_html_report(
        sample_result_set, base=tmp_artifact_root, generated_at=FIXED_GENERATED_AT
    )
    first_bytes = first.read_bytes()

    second = write_html_report(
        sample_result_set, base=tmp_artifact_root, generated_at=FIXED_GENERATED_AT
    )

    assert second == first
    assert second.read_bytes() == first_bytes
    assert file_census(tmp_artifact_root) == {
        paths.target_root(tmp_artifact_root).relative_to(tmp_artifact_root).as_posix(),
        first.relative_to(tmp_artifact_root).as_posix(),
    }


# --------------------------------------------------------------------------- #
# Hook results
#
# A hook is not a step, and on this page it is not only a screenshot carrier
# either.  The port registers the scenario-lifecycle teardown as a real hook,
# so the hook itself can fail - a session that will not quit, a teardown that
# raises - and the element status this page reads folds every hook outcome into
# its verdict.  A page that rendered only the steps therefore presented a red
# scenario above a column of green steps with nothing anywhere to explain it,
# and the hook's status and failure text are what close that gap.
#
# The three properties the block is held to: it reports every hook that did not
# pass, it reports nothing for a run whose hooks passed, and it is not a step -
# not counted as one, not timed as one, and not a filter unit of its own.
# --------------------------------------------------------------------------- #


def test_a_hook_that_did_not_pass_reports_its_status_and_its_error_text() -> None:
    """The failing hook's group, implementation, badge and traceback are shown.

    Driven with a scenario whose every step passed, which is the case the gap
    was about: the element is presented failed because of the hook alone, so
    the hook's own row is the only thing on the page that can account for it.
    The failure text is the hostile value, so the same assertion covers
    escaping and the absence of engine evaluation.
    """
    element = make_element(
        "A scenario whose teardown failed",
        [make_step("a step that passed", status="passed")],
        after=[
            make_hook(
                status="failed",
                error_message=HOSTILE_VALUE,
            )
        ],
        status="failed",
    )
    markup = render_html_report(
        make_result_set(make_feature("Teardown", [element])),
        generated_at=FIXED_GENERATED_AT,
    )
    document = parse_html(markup)
    assert markup_faults(document) == []

    scenario = document.root.with_class(SCENARIO_CLASS)[0]
    blocks = hook_reports(scenario)
    assert len(blocks) == 1, "the failing hook is not reported on the page"
    block = blocks[0]

    assert block.attributes["aria-label"] == (
        f"{AFTER_HOOK_LABEL}{HOOK_REPORT_LABEL_INFIX}"
        "Scenario: A scenario whose teardown failed"
    )
    rows = [row.normalized_text for row in block.with_class(META_ITEM_CLASS)]
    assert rows == [
        f"Hook {AFTER_HOOK_LABEL}",
        f"Implementation {AFTER_HOOK_LOCATION}",
        "Result Failed",
    ]
    assert [
        item.attributes.get("data-report-status")
        for item in block.with_class(BADGE_CLASS)
    ] == ["failed"]
    assert badge_labels(block) == ["Failed"]

    # The failure text is a SIBLING of the block, not a cell inside it: the
    # metadata grid is four columns wide from the 992px breakpoint, and a
    # traceback sized by a quarter-width column wraps at about 28 characters.
    errors = scenario.descendants(ERROR_ELEMENT)
    assert len(errors) == 1
    assert errors[0].text == HOSTILE_VALUE
    assert errors[0].parent is not None
    assert META_CLASS not in errors[0].parent.classes
    assert HOSTILE_MARKUP not in markup
    assert ESCAPED_HOSTILE_MARKUP in markup
    assert HOSTILE_VALUE_EVALUATED not in document.root.text

    # The scenario's own badge is still the first one inside it, so the
    # element's status reading is untouched by the block added below it.
    assert first_badge_label(scenario) == "Failed"


def test_a_hook_status_outside_the_vocabulary_keeps_the_word_the_model_recorded()  -> None:
    """A behave-only hook status is graded once, and an unreadable one keeps its word.

    Two halves, because the shared status model decides the first of them.
    ``hook_error`` is behave's name for an exception in hook code and
    ``app/reporting/aggregation.py``'s ``STATUS_ALIASES`` folds it onto
    ``failed`` -- the same answer ``target/cucumber.json`` publishes for it,
    that table existing precisely so one run is not graded differently
    depending on which artifact a reader opens.  The fold is applied to the
    decorated copy this writer renders, so the page reports such a hook
    **Failed** and the behave word is not on it.

    The second half is the clause that word reaches when nothing in the project
    can read the status: the block is still emitted, the badge reads Unknown
    because a badge's label is its status's accessible name on every surface of
    this port, and the recorded word travels as text beside the
    implementation, since a row reading only "Unknown" would withhold the one
    word naming what happened.  It is asserted against
    ``artifact/element.html``'s own macro with an undecorated element, which is
    the only shape that clause is reachable from once the authority
    canonicalises every status it can name.  Both hook groups are covered in
    each half, in the order the model carries them.
    """
    element = make_element(
        "A scenario with two bad hooks",
        [make_step("a step that passed", status="passed")],
        before=[make_hook(status=BEHAVE_HOOK_ERROR_STATUS, location=None)],
        after=[
            make_hook(
                status=BEHAVE_HOOK_ERROR_STATUS,
                error_message="RuntimeError: teardown exploded",
            )
        ],
        status="passed",
    )
    document = parse_html(
        render_html_report(
            make_result_set(make_feature("Hooks", [element])),
            generated_at=FIXED_GENERATED_AT,
        )
    )
    assert markup_faults(document) == []

    scenario = document.root.with_class(SCENARIO_CLASS)[0]
    blocks = hook_reports(scenario)
    assert len(blocks) == 2, "both hook groups are reported"

    # Named per hook, and numbered because this element has more than one, so
    # two blocks of one page never share an accessible name.
    heading = "Scenario: A scenario with two bad hooks"
    assert [block.attributes["aria-label"] for block in blocks] == [
        f"{BEFORE_HOOK_LABEL} 1{HOOK_REPORT_LABEL_INFIX}{heading}",
        f"{AFTER_HOOK_LABEL} 2{HOOK_REPORT_LABEL_INFIX}{heading}",
    ]

    # The before-hook carried no location and the model's canonical word for
    # both of these is one the page's own vocabulary names, so there is no
    # parenthesis to carry and the location-less block states no
    # implementation at all rather than an empty row.
    assert [row.normalized_text for row in blocks[0].with_class(META_ITEM_CLASS)] == [
        f"Hook {BEFORE_HOOK_LABEL}",
        "Result Failed",
    ]
    assert [row.normalized_text for row in blocks[1].with_class(META_ITEM_CLASS)] == [
        f"Hook {AFTER_HOOK_LABEL}",
        f"Implementation {AFTER_HOOK_LOCATION}",
        "Result Failed",
    ]

    assert [error.text for error in scenario.descendants(ERROR_ELEMENT)] == [
        "RuntimeError: teardown exploded"
    ]
    assert badge_labels(blocks[0]) == ["Failed"]
    assert badge_labels(blocks[1]) == ["Failed"]
    assert status_token(BEHAVE_HOOK_ERROR_STATUS) == "failed"
    assert STATUS_ALIASES[BEHAVE_HOOK_ERROR_STATUS] == "failed"

    # The unreadable status, through the template's own macro and with no
    # decoration in front of it: reported, badged Unknown, and the word kept
    # beside the implementation -- or standing in for the one the hook has not
    # got.
    fragment = parse_html(
        str(
            build_environment()
            .get_template("artifact/element.html")
            .module.element_block(  # type: ignore[attr-defined]
                make_element(
                    "A scenario with two unreadable hook statuses",
                    [make_step("a step that passed", status="passed")],
                    before=[make_hook(status=GARBAGE_STATUS, location=None)],
                    after=[
                        make_hook(
                            status=GARBAGE_STATUS,
                            error_message="RuntimeError: teardown exploded",
                        )
                    ],
                    status="passed",
                ),
                0,
                0,
            )
        )
    )
    unreadable = hook_reports(fragment.root)
    assert len(unreadable) == 2, "both hook groups are reported"
    assert [
        row.normalized_text for row in unreadable[0].with_class(META_ITEM_CLASS)
    ] == [
        f"Hook {BEFORE_HOOK_LABEL}",
        f"Implementation {GARBAGE_STATUS}",
        f"Result {UNKNOWN_STATUS.capitalize()}",
    ]
    assert [
        row.normalized_text for row in unreadable[1].with_class(META_ITEM_CLASS)
    ] == [
        f"Hook {AFTER_HOOK_LABEL}",
        f"Implementation {AFTER_HOOK_LOCATION} ({GARBAGE_STATUS})",
        f"Result {UNKNOWN_STATUS.capitalize()}",
    ]
    assert badge_labels(unreadable[0]) == [UNKNOWN_STATUS.capitalize()]
    assert badge_labels(unreadable[1]) == [UNKNOWN_STATUS.capitalize()]
    assert status_token(GARBAGE_STATUS) == UNKNOWN_STATUS


def test_a_hook_that_passed_is_not_reported_and_its_screenshot_still_is() -> None:
    """The ordinary shape adds not one node, and the embedding is unaffected.

    Every scenario of a healthy run carries a passed after-hook, and the sample
    document's own failing scenario carries one with a screenshot on it, so a
    block emitted for a hook that passed would appear on nearly every element
    of every page.  The screenshot is asserted alongside, because the hook list
    feeds both this block and the lightbox and the two must stay independent.
    """
    element = make_element(
        "A scenario whose teardown passed",
        [make_step("a step that failed", status="failed", error_message="boom")],
        after=[
            make_hook(
                status="passed",
                embeddings=[make_embedding(name="A scenario whose teardown passed")],
            )
        ],
        status="failed",
    )
    document = parse_html(
        render_html_report(
            make_result_set(make_feature("Healthy hooks", [element])),
            generated_at=FIXED_GENERATED_AT,
        )
    )
    assert markup_faults(document) == []

    assert hook_reports(document.root) == []
    assert AFTER_HOOK_LABEL not in document.root.text
    assert len(screenshot_images(document.root)) == 1


def test_hook_reports_are_not_steps_and_carry_neither_duration_nor_filter_hook() -> None:
    """The block is outside the step and filter vocabularies, and untimed.

    Three properties in one case, because they are one decision: a hook is not
    a step.  So the block holds no step container - nothing that counts or
    walks steps can pick a hook up - it carries no filter hook, the element
    being this page's filter unit and a nested one hiding a subtree of the
    thing being hidden, and it prints no duration, the element duration this
    report presents being the sum of its step durations alone.  The last is
    asserted against the page rendered without the hook, so it is the hook's
    duration that is absent rather than some number being absent.
    """
    steps = [make_step("a step that passed", status="passed")]
    without_hook = make_element("Timed", steps, status="passed")
    with_hook = make_element(
        "Timed",
        steps,
        after=[make_hook(status="failed", error_message="teardown failed")],
        status="failed",
    )

    plain = parse_html(
        render_html_report(
            make_result_set(make_feature("Durations", [without_hook])),
            generated_at=FIXED_GENERATED_AT,
        )
    )
    hooked = parse_html(
        render_html_report(
            make_result_set(make_feature("Durations", [with_hook])),
            generated_at=FIXED_GENERATED_AT,
        )
    )

    scenario = hooked.root.with_class(SCENARIO_CLASS)[0]
    block = hook_reports(scenario)[0]
    assert block.with_class(STEP_CLASS) == []
    assert len(hooked.root.with_class(STEP_CLASS)) == len(
        plain.root.with_class(STEP_CLASS)
    )
    assert [
        element
        for element in block.walk()
        if FILTERABLE_ATTRIBUTE in element.attributes
    ] == []
    # The element is the filter unit, so the traceback beside the block is not
    # a second one either.
    assert [
        error
        for error in scenario.descendants(ERROR_ELEMENT)
        if FILTERABLE_ATTRIBUTE in error.attributes
    ] == []

    # The hook duration is not on the page in any of the three forms the page
    # could print it in: raw nanoseconds, or either of the two second-scale
    # renderings the duration helpers produce.
    page_text = hooked.root.normalized_text
    for rendering in (
        str(HOOK_DURATION),
        f"{HOOK_DURATION / 1_000_000_000:.3f}",
        f"{HOOK_DURATION / 1_000_000_000:.2f}",
    ):
        assert rendering not in page_text, rendering
