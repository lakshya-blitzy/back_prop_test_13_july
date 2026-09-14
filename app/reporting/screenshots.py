"""Failure-screenshot capture, encoding and embedding.

This module is the Python port of the screenshot half of ``Hooks.java`` in the
Java implementation this project reproduces::

    @After
    public void teardownScenario(Scenario scenario){
        if(scenario.isFailed()){
            byte [] screenshot = ((TakesScreenshot) Driver.getDriver()).getScreenshotAs(OutputType.BYTES);
            scenario.attach(screenshot, "image/png", scenario.getName());
        }
        Driver.closeDriver();
    }

    -- Hooks.java:11-18

Four behaviours follow from those eight lines, and all four are preserved here:

1. Capture happens **only when the scenario failed**.  There is no else-branch
   in the source and there is no passing-scenario path here.  The failure test
   itself is *not* performed by this module: ``features/environment.py`` owns
   it, exactly as the Java ``if(scenario.isFailed())`` sits in the hook rather
   than in the capture call.
2. Capture happens **exactly once** per scenario -- one call, one image.
3. Capture happens **before the driver is quit**; ``Driver.closeDriver()`` is
   the statement immediately after the attach.  Accepting the driver as a
   parameter is what keeps that ordering the caller's to enforce, and makes it
   enforceable: the live session is passed in while it is still live.
4. The attachment carries exactly **three** things -- the PNG bytes, the MIME
   type ``"image/png"``, and the scenario's name.

**This hook never runs in the source.**  ``Hooks.java:5`` imports
``org.junit.After`` rather than ``io.cucumber.java.After``, so Cucumber never
invokes the teardown; the reference build's JSON report corroborates it by
containing no ``embeddings`` key and no ``"after"`` key anywhere.  Conflict 6 /
deviation 19's sibling deviation 6 of the plan *corrects* that defect rather
than reproducing it, because reproducing it would ship the framework's
screenshot capability dead on arrival.  The capability therefore exists in
intent but has never executed, and the embedding shape below is derived from
the ``scenario.attach`` call plus the report generator's own
``createEmbeddingMap`` -- not from an observed artifact.

Design constraints, each of which is a plan requirement rather than a
preference:

* **No Selenium import.**  ``app/automation`` is the only package permitted to
  import the Selenium bindings, and this is not it.  No Selenium type is
  needed: ``get_screenshot_as_png()`` is duck-typed, and the private
  :class:`_TakesScreenshot` protocol below plays the role the Java cast to
  ``TakesScreenshot`` plays -- documentation of the one method required, with
  no runtime coupling.
* **No driver lookup.**  ``get_driver()`` is not called and
  ``app/automation`` is not imported.  The reporting package has exactly one
  outgoing intra-package dependency edge in the plan's dependency graph, to
  ``app/utils``, and this module needs nothing even from there.
* **Never changes a test outcome.**  A screenshot is evidence about a result,
  never part of one.  Every failure path here logs and returns ``None``; no
  exception escapes :func:`capture_failure_embedding`.
* **A suppressed failure says which scenario lost its evidence.**  Because the
  failure is suppressed, the log record is the only trace it leaves, and in a
  shard that ran many scenarios a fixed message cannot be attributed to one of
  them.  Both capture entry points therefore accept a keyword-only
  ``scenario_id`` -- the caller's diagnostic identity, which
  ``features/environment.py`` builds from the scenario's feature file, line
  and name -- and render it with ``%r`` so a control character in a scenario
  name cannot break the record apart.  It is logged once per failure and
  nothing else about the attempt is: no image bytes and no base64 payload ever
  reach a log record.  Omitting it leaves the record exactly as it read
  before, never the word ``None``.
* **No enable flag.**  ``README.md:42-43`` claims screenshots "for your tests
  if you enable it", but no setting and no branch in ``Hooks`` provides one, so
  under the plan's precedence rule that claim is aspirational.  The
  configuration surface stays at its six keys -- ``browser``,
  ``web.table.url``, ``url``, ``username``, ``password``, ``EmplTitle`` -- none
  of which concerns screenshots, and ``app/config.py`` is not imported.
* **Nothing is written to disk.**  The embedding is inline base64; no path
  module defines a screenshot location, and writing one would break the
  self-contained guarantee of the single-page HTML report.
* **The image is not transformed.**  No resize, crop, annotation or format
  conversion, and no imaging dependency: the source POM declares none.

Consumers of :func:`build_embedding`'s return value:

* ``app/reporting/cucumber_json.py`` serialises it into the failed scenario
  element's ``after`` entry as the single member of its ``embeddings`` array.
* ``app/templates/partials/lightbox.html`` renders it as
  ``data:<mime_type>;base64,<data>`` behind the report lightbox.

Both are written against the keys ``mime_type``, ``data`` and ``name``.  The
underscore spelling of ``mime_type`` is the contract, not a typo: the Java
generator's own source notes that the field should have been named for the
media type and was not worth migrating, so the field name it emits is the one
downstream readers -- including the Cucumber publisher -- expect.

**The embedding contract is validated here, once, for every consumer.**  An
attachment reaches the two HTML writers as result-controlled data: it arrives
in the merged result set, which is assembled from whatever the per-worker
event files contain, and both writers turn it into a ``data:`` URI inside a
document a human then opens.  A media type taken from that data would let a
result choose the media type of an inline document -- ``image/svg+xml`` is an
executable document in a browser -- and a payload accepted on the strength of
its declared media type would let base64 of something that is not an image
through under the name of one.  Neither is acceptable, so this module owns the
one strict test both writers apply before rendering, and
:func:`normalize_embedding` is the only sanctioned way to obtain a renderable
attachment.  Four independent things are established, in this order:

1. the normalized media type is **exactly** ``image/png`` -- not a prefix
   match, not a parameterised variant;
2. the base64 is **canonical** -- the standard alphabet, correct padding, and
   zero unused pad bits, proven by decoding the payload strictly and
   re-encoding the result to a byte-identical string;
3. the decoded bytes begin with the **complete eight-byte** PNG signature
   ``89 50 4E 47 0D 0A 1A 0A``, so the payload is a PNG rather than base64 of
   anything else;
4. the decoded image is within :data:`MAX_EMBEDDING_BYTES`, checked against
   the encoded length *before* decoding so an oversized payload is rejected
   without being expanded in memory.

An attachment failing any of the four is **discarded and logged**, never
repaired and never rendered.  The template and script guards in
``app/templates/partials/lightbox.html`` and ``app/static/js/report.js`` apply
the same four rules independently; that duplication is defence in depth, not
the primary control, because a report is also rendered from a result set this
module never saw -- the Flask viewer reads the merged JSON report directly.
"""

from __future__ import annotations

import base64
import binascii
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Final, Protocol

__all__ = [
    "CAPTURE_FAILURE_MESSAGE",
    "DEFAULT_MIME_TYPE",
    "INVALID_EMBEDDING_MESSAGE",
    "MAX_EMBEDDING_BASE64_CHARS",
    "MAX_EMBEDDING_BYTES",
    "PNG_BASE64_SIGNATURE",
    "PNG_SIGNATURE",
    "build_embedding",
    "canonical_png_payload",
    "capture_failure_embedding",
    "capture_png",
    "decode_png_payload",
    "encode_png",
    "is_png_bytes",
    "normalize_element_attachments",
    "normalize_embedding",
    "normalize_embeddings",
    "normalize_feature_attachments",
    "normalize_features_attachments",
]

# Module logger.  Deliberately left without a handler of its own -- in
# particular without a ``logging.NullHandler`` -- because the plan requires
# capture failures to reach **stderr**.  The command-line entry point installs
# the handler split that routes WARNING-and-above to stderr; when logging has
# not been configured at all (a bare ``import`` in a test, say), Python's
# ``logging.lastResort`` handler still emits WARNING-and-above to stderr.
# Attaching a NullHandler here would satisfy neither, because it would count as
# a handler and silence both routes.  Nothing in this module writes to
# ``sys.stderr`` directly: stderr routing is achieved by log level.
logger = logging.getLogger(__name__)

#: The MIME type the source attaches its screenshot bytes under, verbatim from
#: ``Hooks.java:15``.  Not configurable -- see the module docstring on the
#: absence of an enable flag; the same reasoning forbids a format switch.
DEFAULT_MIME_TYPE: Final[str] = "image/png"

#: The message logged when capture, encoding or embedding construction fails.
#: Exposed as a symbol so that callers and tests assert against a constant
#: rather than against a duplicated string literal.  Every suppression record
#: this module emits is built *around* this constant by
#: :func:`_suppression_record`, never by rewriting it, so the constant stays
#: the stable substring downstream assertions match on.
CAPTURE_FAILURE_MESSAGE: Final[str] = (
    "Failure screenshot could not be captured; no embedding will be emitted "
    "and the scenario status is unchanged"
)

#: How a caller-supplied scenario identity is rendered into a suppression
#: record.  ``%r`` rather than ``%s`` on purpose: the identity is built from
#: feature text, so ``repr`` is what keeps a newline, a tab or any other
#: control character in a scenario name from breaking the log line apart, and
#: it delimits the value so an empty identity is still visible.
_IDENTITY_SEGMENT: Final[str] = "scenario %r"


def _suppression_record(
    scenario_id: str | None,
    detail_format: str = "",
    *detail_args: object,
) -> tuple[str, tuple[object, ...]]:
    """Compose one suppression log record: its format string and its arguments.

    Every suppressed failure in this module is reported exactly once, and this
    is where the record's shape is decided so the three call sites cannot
    drift apart.  Each of them unpacks the result as ``fmt, args`` and logs
    ``logger.error(fmt, *args)``, which hands the format string and its
    arguments to the standard library separately and so keeps its lazy
    ``%``-interpolation: a record that is filtered out is never rendered.

    Args:
        scenario_id: The caller's diagnostic identity for the scenario whose
            evidence is missing -- in this port the feature URI, the line and
            the scenario name, supplied by ``features/environment.py``.
            ``None`` (the default everywhere) degrades the record to
            :data:`CAPTURE_FAILURE_MESSAGE` exactly as it read before an
            identity could be passed, rather than logging ``None``.
        detail_format: An optional extra segment, as a ``%``-format fragment,
            for a failure that has something to add beyond the exception the
            logger is already carrying.
        *detail_args: The arguments ``detail_format`` interpolates.

    Returns:
        The format string and the argument tuple.  Never the image bytes, the
        base64 payload or any part of either: a suppression record identifies
        *which* scenario lost its evidence and nothing about the picture.
    """
    segments: list[str] = []
    args: list[object] = []

    if scenario_id is not None:
        segments.append(_IDENTITY_SEGMENT)
        args.append(scenario_id)

    if detail_format:
        segments.append(detail_format)
        args.extend(detail_args)

    if not segments:
        # Byte-for-byte the record this module emitted before identities
        # existed, including ``msg`` being the constant itself rather than a
        # format string that renders to it.
        return CAPTURE_FAILURE_MESSAGE, ()

    return "%s (" + "; ".join(segments) + ")", (CAPTURE_FAILURE_MESSAGE, *args)


#: The message logged when a result-controlled attachment is discarded because
#: it is not a PNG this project will render.  Discarding is the specified
#: behaviour: an attachment is evidence about a result, never part of one, so a
#: rejected attachment removes the evidence and leaves every status untouched.
INVALID_EMBEDDING_MESSAGE: Final[str] = (
    "Attachment discarded: not a valid inline PNG embedding; no screenshot "
    "will be rendered for it and no result status is affected"
)

#: PNG's file signature, all **eight** bytes of it, from the specification's
#: first clause: a high-bit byte that survives no 7-bit transfer, the ASCII
#: letters ``PNG``, a CRLF pair, a DOS end-of-file byte and a bare LF.  The
#: full eight are checked: the first six alone are also the first six bytes of
#: a payload whose seventh and eighth bytes are anything at all, so a
#: six-byte test accepts base64 that is not a PNG.
PNG_SIGNATURE: Final[bytes] = b"\x89PNG\r\n\x1a\x0a"

#: The largest decoded attachment this project will render inline, in bytes.
#: A bound is required because the payload is result-controlled and is decoded
#: in memory, and because both HTML writers inline it into a single document a
#: human opens -- an unbounded attachment makes that document unopenable.  32
#: MiB is far above any viewport screenshot (a 4K PNG runs to single-digit
#: megabytes) and far below a size that threatens the writer.
MAX_EMBEDDING_BYTES: Final[int] = 32 * 1024 * 1024

#: The encoded length that bound implies.  Standard base64 emits four
#: characters per three bytes, rounded up to a multiple of four, so this is the
#: longest payload worth decoding -- and checking it first is what keeps an
#: oversized payload from being expanded in memory before it is rejected.
MAX_EMBEDDING_BASE64_CHARS: Final[int] = ((MAX_EMBEDDING_BYTES + 2) // 3) * 4

#: The fixed base64 prefix of the PNG signature, for the two guards that
#: cannot decode: a Jinja template and, historically, a character-scanning
#: script.  Base64 maps each three bytes onto four characters, so characters
#: 1-8 are fixed by signature bytes 1-6 and characters 9-11 carry bytes 7 and
#: 8 together with the top two bits of byte 9.  Byte 9 is the high byte of the
#: length of a PNG's first chunk, which is always IHDR at 13 bytes, so those
#: two bits are always zero and the eleventh character is always ``o``.  This
#: prefix therefore establishes the complete eight-byte signature and rejects
#: nothing a real PNG produces.  Asserted against a genuine PNG in the
#: contract tests rather than trusted as arithmetic.
PNG_BASE64_SIGNATURE: Final[str] = "iVBORw0KGgo"


def is_png_bytes(data: object) -> bool:
    """Whether ``data`` is a byte string beginning with PNG's full signature.

    Args:
        data: Any object.  Only ``bytes`` can qualify; a ``bytearray`` or
            ``memoryview`` is rejected rather than coerced, because the only
            callers here have already normalised their payload and a silent
            coercion would hide the one that had not.

    Returns:
        ``True`` only for ``bytes`` whose first eight bytes are
        :data:`PNG_SIGNATURE`.  A payload shorter than the signature, a
        payload whose seventh and eighth bytes differ, and every non-bytes
        object answer ``False``.
    """
    return isinstance(data, bytes) and data.startswith(PNG_SIGNATURE)


def decode_png_payload(payload: object) -> bytes | None:
    """Strictly decode a base64 attachment payload, or answer ``None``.

    This is the single test the three guards of this project agree on, and
    every clause of it exists because a weaker clause admits something
    concrete:

    * **Length.**  An encoded length that is not a positive multiple of four
      cannot be canonical base64.  The upper bound is checked here, before the
      decode, so an oversized payload is never expanded.
    * **Alphabet and padding.**  ``base64.b64decode(..., validate=True)``
      raises on any character outside the standard alphabet -- including the
      whitespace and newlines the lenient decoder silently drops, and the
      ``-``/``_`` of the URL-safe alphabet -- and on padding that is absent,
      excessive or misplaced.
    * **Canonicality.**  Strict decoding still accepts non-zero *unused pad
      bits*: ``iVBORw0KAB==`` decodes happily although the canonical encoding
      of those same bytes is ``iVBORw0KAA==``.  Two encodings of one byte
      string is exactly the ambiguity a downstream reader must not have to
      resolve, so the decoded bytes are re-encoded and required to reproduce
      the payload byte for byte.
    * **Signature.**  The decoded bytes must begin with the complete
      eight-byte :data:`PNG_SIGNATURE`.  Checking six bytes accepts
      ``iVBORw0KAAAA``, which is base64 of a byte string that is not a PNG.

    Args:
        payload: The attachment's ``data`` value, as it arrived in the result
            set.  Any type is accepted and anything that is not a ``str`` is
            rejected.

    Returns:
        The decoded PNG bytes when every clause holds, and ``None`` otherwise.
        Never raises: a decoding error is a rejection, not an exception, so a
        malformed attachment cannot break a report write.

    Examples:
        >>> import base64
        >>> png = PNG_SIGNATURE + b"the rest of a file"
        >>> decode_png_payload(base64.b64encode(png).decode()) == png
        True
        >>> decode_png_payload("iVBORw0KAAAA") is None   # bytes 7-8 wrong
        True
        >>> decode_png_payload("iVBORw0KAB==") is None   # non-zero pad bits
        True
        >>> decode_png_payload("<svg/>") is None
        True
    """
    if not isinstance(payload, str):
        return None
    if not payload or len(payload) % 4 != 0:
        return None
    if len(payload) > MAX_EMBEDDING_BASE64_CHARS:
        return None

    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        # binascii.Error is a ValueError subclass; both are named so the intent
        # survives a future change to either. Every other exception type would
        # be a programming error and is deliberately not swallowed.
        return None

    # Canonicality, and with it the padding-bit test no character scan can make.
    if base64.b64encode(decoded).decode("ascii") != payload:
        return None
    if not is_png_bytes(decoded):
        return None
    return decoded


def canonical_png_payload(payload: object) -> str | None:
    """Return the canonical base64 of a valid PNG payload, or ``None``.

    Args:
        payload: The attachment's ``data`` value.

    Returns:
        The canonical encoding of the decoded bytes, which
        :func:`decode_png_payload` has already proven equal to ``payload``, or
        ``None`` when the payload is not a renderable PNG.  Re-encoding rather
        than returning the input is what makes the emitted value's canonicality
        a property of this function instead of a property of its caller's
        input.
    """
    decoded = decode_png_payload(payload)
    if decoded is None:
        return None
    return base64.b64encode(decoded).decode("ascii")


def normalize_embedding(embedding: object) -> dict[str, str] | None:
    """Validate one result-controlled attachment into a renderable embedding.

    The media type is **not** carried over from the input.  It is compared
    against :data:`DEFAULT_MIME_TYPE` and then written from that constant, so
    the value reaching a ``data:`` URI is a literal of this project's and can
    never be chosen by a result -- which is the whole of the media-type half of
    the contract.

    Args:
        embedding: A candidate attachment mapping, normally with the keys
            ``mime_type``, ``data`` and optionally ``name``.  Any object is
            accepted; anything that is not a mapping is rejected.

    Returns:
        A new mapping carrying the fixed ``mime_type``, the canonical
        ``data``, and ``name`` when the input carried one, or ``None`` when the
        attachment is not a renderable PNG.  The name is carried verbatim --
        escaping belongs to the serialising consumer -- but a non-string name
        is dropped rather than coerced, so no value can surface in a page as
        the text ``None``.

    Notes:
        Never raises.  A rejection is logged once at warning level, which the
        CLI's handler split routes to stderr, and the caller simply has one
        fewer attachment.
    """
    if not isinstance(embedding, Mapping):
        logger.warning("%s (not a mapping: %s)", INVALID_EMBEDDING_MESSAGE, type(embedding).__name__)
        return None

    media_type = embedding.get("mime_type")
    if not isinstance(media_type, str) or media_type.strip().lower() != DEFAULT_MIME_TYPE:
        logger.warning(
            "%s (media type %r is not %s)",
            INVALID_EMBEDDING_MESSAGE,
            media_type,
            DEFAULT_MIME_TYPE,
        )
        return None

    payload = canonical_png_payload(embedding.get("data"))
    if payload is None:
        logger.warning(
            "%s (payload is not canonical base64 of a PNG, or exceeds %d bytes decoded)",
            INVALID_EMBEDDING_MESSAGE,
            MAX_EMBEDDING_BYTES,
        )
        return None

    normalized: dict[str, str] = {"mime_type": DEFAULT_MIME_TYPE, "data": payload}
    name = embedding.get("name")
    if isinstance(name, str):
        normalized["name"] = name
    return normalized


def normalize_embeddings(value: object) -> list[dict[str, str]]:
    """Validate a list of attachments, dropping every one that does not qualify.

    Args:
        value: The ``embeddings`` value as it arrived -- normally a list of
            mappings.  A string, a mapping and ``None`` are each treated as
            "no attachments" rather than iterated, because iterating a string
            yields characters and iterating a mapping yields keys, and neither
            is an attachment.

    Returns:
        A new list, in input order, of the attachments that passed
        :func:`normalize_embedding`.  Empty when none did, which both HTML
        writers and the shared partial render as nothing at all.
    """
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        return []
    return [
        normalized
        for normalized in (normalize_embedding(item) for item in value)
        if normalized is not None
    ]


def _normalize_hook(hook: object) -> dict[str, Any] | None:
    """Return a copy of one after-hook entry with its attachments validated.

    Args:
        hook: A candidate hook entry.  A non-mapping is dropped.

    Returns:
        A shallow copy carrying a validated ``embeddings`` list when the entry
        declared one, the entry's copy unchanged when it declared none, or
        ``None`` for an entry that is not a mapping.  A hook whose every
        attachment was rejected keeps an empty list rather than losing the key,
        so a template asking whether the hook had attachments still gets a
        truthful answer about its own shape.
    """
    if not isinstance(hook, Mapping):
        return None
    if "embeddings" not in hook:
        return dict(hook)
    return {**hook, "embeddings": normalize_embeddings(hook.get("embeddings"))}


def normalize_element_attachments(element: object) -> dict[str, Any]:
    """Return a copy of one scenario or Background element, attachments validated.

    Both shapes the report templates read are covered, because both occur: an
    ``after`` list of hook entries each carrying ``embeddings`` -- which is what
    the JSON schema defines and what this project's own hook produces -- and a
    flat ``embeddings`` list directly on the element, which
    ``artifact/element.html`` and the two viewer templates also accept.

    Args:
        element: A candidate element mapping.  A non-mapping answers an empty
            mapping, which every caller here renders as an element with
            nothing in it rather than raising.

    Returns:
        A new mapping.  Keys the element did not declare are **not** added:
        an element with no attachments is copied unchanged, so this function
        cannot make an element look as though it carried evidence it did not.
        Nothing is mutated -- one merged result set feeds four writers, and a
        writer that edited it in place would change what the others see.
    """
    if not isinstance(element, Mapping):
        return {}
    normalized: dict[str, Any] = dict(element)
    if "embeddings" in normalized:
        normalized["embeddings"] = normalize_embeddings(normalized.get("embeddings"))
    if "after" in normalized:
        hooks = normalized.get("after")
        if isinstance(hooks, (str, bytes, Mapping)) or not isinstance(hooks, Sequence):
            normalized["after"] = []
        else:
            normalized["after"] = [
                hook
                for hook in (_normalize_hook(entry) for entry in hooks)
                if hook is not None
            ]
    return normalized


def normalize_feature_attachments(feature: object) -> dict[str, Any]:
    """Return a copy of one feature with every element's attachments validated.

    Args:
        feature: A candidate feature mapping.  A non-mapping answers an empty
            mapping.

    Returns:
        A new mapping whose ``elements``, when it declared any, is a new list
        of validated element copies in their original order -- features in
        source order, elements exactly where the model puts them, each
        Background occurrence repeated in its own position, all of which the
        report contract fixes.  A feature declaring no elements is copied
        unchanged.
    """
    if not isinstance(feature, Mapping):
        return {}
    if "elements" not in feature:
        return dict(feature)
    elements = feature.get("elements")
    if isinstance(elements, (str, bytes, Mapping)) or not isinstance(elements, Sequence):
        return {**feature, "elements": []}
    return {
        **feature,
        "elements": [normalize_element_attachments(element) for element in elements],
    }


def normalize_features_attachments(features: object) -> list[dict[str, Any]]:
    """Validate the attachments of every feature in a sequence.

    This is the call both HTML writers make, at the one point each assembles
    the features it is about to render, so no template of either writer can be
    reached by an attachment that has not been through
    :func:`normalize_embedding`.

    Args:
        features: The features to render.  A string, a mapping and ``None`` are
            each treated as no features.

    Returns:
        A new list of validated feature copies, in input order.
    """
    if isinstance(features, (str, bytes, Mapping)) or not isinstance(features, Sequence):
        return []
    return [normalize_feature_attachments(feature) for feature in features]


class _TakesScreenshot(Protocol):
    """Structural stand-in for Selenium's ``TakesScreenshot`` interface.

    The Java hook casts its driver to ``TakesScreenshot`` before asking for
    bytes.  This protocol documents the same single-method requirement without
    importing the Selenium bindings, which this package may not do.  It is
    used for type hints only: no ``isinstance`` check is performed anywhere, so
    any object exposing ``get_screenshot_as_png()`` -- a real WebDriver session
    or a test stub -- is accepted, and an object *lacking* it takes the same
    suppressed-failure path as a driver whose session has died.
    """

    def get_screenshot_as_png(self) -> bytes:
        """Return the current browser viewport encoded as PNG bytes."""


def capture_png(
    driver: _TakesScreenshot,
    *,
    scenario_id: str | None = None,
) -> bytes | None:
    """Capture the current browser viewport as PNG bytes.

    This is the port of ``((TakesScreenshot) Driver.getDriver())``
    ``.getScreenshotAs(OutputType.BYTES)`` at ``Hooks.java:14``.  The driver
    arrives as a parameter rather than being looked up, so the caller -- which
    owns the scenario lifecycle -- can guarantee the session is still live.

    Args:
        driver: Any object exposing ``get_screenshot_as_png()``.  In production
            this is the live WebDriver session for the failed scenario, taken
            from the driver holder *before* it is quit.
        scenario_id: Keyword-only diagnostic identity for the scenario being
            photographed, included in whatever suppression record this call
            emits.  ``features/environment.py`` builds it from the scenario's
            feature file, line and name, which is what makes missing evidence
            attributable in a shard that ran many scenarios.  It is never part
            of the returned value, never written to an artifact and never
            compared against anything; omitting it leaves the record reading
            exactly as it did before.

    Returns:
        The PNG bytes, or ``None`` if no usable image could be obtained.

    Notes:
        Never raises.  A dead or crashed session, a driver that does not expose
        the method at all, or a driver returning an unusable payload are all
        logged at error level and reported as ``None``.  That suppression is a
        deliberate deviation recorded in the plan: a screenshot failure must not
        be able to change a test outcome, and a ``None`` return is
        indistinguishable downstream from "no screenshot was taken".
    """
    try:
        png = driver.get_screenshot_as_png()
    except Exception:
        # Broad on purpose.  A dead session raises ``WebDriverException`` and a
        # driver lacking the method raises ``AttributeError``; the first cannot
        # be named without importing Selenium, and neither may be allowed to
        # propagate.  ``BaseException`` is *not* caught: KeyboardInterrupt and
        # SystemExit must still end the run.
        fmt, args = _suppression_record(scenario_id)
        logger.exception(fmt, *args)
        return None

    # Defensive normalisation.  A well-behaved driver returns non-empty
    # ``bytes``; anything else would end up as a truncated or empty ``data:``
    # URI in both HTML artifacts, which renders as a broken image and is worse
    # than no embedding at all.  Such a payload is therefore treated exactly as
    # a capture failure: logged, and reported as ``None``.
    if isinstance(png, (bytearray, memoryview)):
        png = bytes(png)
    if not isinstance(png, bytes) or not png:
        # The payload's *type name* is diagnostic; the payload itself is not
        # logged, here or anywhere else in this module.
        fmt, args = _suppression_record(
            scenario_id,
            "driver returned an unusable screenshot payload: %s",
            type(png).__name__,
        )
        logger.error(fmt, *args)
        return None

    # The payload must be what this function's name promises.  A driver that
    # answered a JPEG, an error page or a truncated file would otherwise be
    # embedded under the media type ``image/png`` -- the one thing the
    # embedding contract forbids -- and the size bound is applied here for the
    # same reason it is applied to a result-controlled payload: the image is
    # inlined into a single document a human opens.
    if not is_png_bytes(png):
        fmt, args = _suppression_record(
            scenario_id,
            "driver returned %d bytes that do not begin with PNG's signature",
            len(png),
        )
        logger.error(fmt, *args)
        return None
    if len(png) > MAX_EMBEDDING_BYTES:
        fmt, args = _suppression_record(
            scenario_id,
            "screenshot is %d bytes, above the %d-byte inline limit",
            len(png),
            MAX_EMBEDDING_BYTES,
        )
        logger.error(fmt, *args)
        return None

    return png


def encode_png(png: bytes) -> str:
    """Base64-encode PNG bytes for inline embedding.

    Args:
        png: The raw image bytes to encode.

    Returns:
        The standard base64 representation as an ASCII ``str``, containing no
        line breaks.

    Raises:
        TypeError: If ``png`` is not a bytes-like object.  Callers inside this
            module only ever pass a value already validated by
            :func:`capture_png`, and :func:`capture_failure_embedding` suppresses
            the error in any case; the exception is left visible here so that a
            genuine programming error in a future caller is not hidden.

    Notes:
        ``base64.b64encode`` is used rather than ``base64.encodebytes``: the
        latter inserts a newline every 76 characters, which would corrupt the
        ``data:<mime_type>;base64,<data>`` URI both HTML report writers build
        from this value.  The unchunked encoder is also what the Java generator
        uses -- ``Base64.getEncoder().encodeToString(data)`` -- so the encoded
        payload matches the source contract byte for byte.
    """
    return base64.b64encode(png).decode("ascii")


def build_embedding(
    png: bytes,
    name: str | None,
    mime_type: str = DEFAULT_MIME_TYPE,
) -> dict[str, str]:
    """Build the embedding mapping the report writers serialise.

    This is the port of ``scenario.attach(screenshot, "image/png",
    scenario.getName())`` at ``Hooks.java:15``, shaped as the JSON report
    generator's ``createEmbeddingMap`` shapes it.

    Args:
        png: The raw PNG bytes to embed.
        name: The attachment's name -- the scenario's name in this port.  When
            ``None`` the ``"name"`` key is **omitted** from the result rather
            than emitted as ``null``, mirroring the generator's
            ``if (name != null) embedMap.put("name", name)``.  Because the
            caller always supplies the scenario name, that branch is defensive,
            but it is implemented faithfully so the emitted JSON cannot acquire
            a key the source would not have written.
        mime_type: The attachment's MIME type, defaulting to
            :data:`DEFAULT_MIME_TYPE`.  Present because the generator's field is
            per-attachment, not because the format is configurable; no setting
            reaches this parameter, and a value other than ``image/png`` is
            rejected rather than honoured.

    Returns:
        A mapping with the keys ``mime_type`` and ``data``, plus ``name`` when
        a name was supplied.  The name is carried verbatim -- quotation marks,
        apostrophes and non-ASCII characters in scenario names are preserved,
        and JSON or HTML escaping is the serialising consumer's concern, not
        this function's.

    Raises:
        TypeError: Propagated from :func:`encode_png` if ``png`` is not
            bytes-like.
        ValueError: If ``mime_type`` is not ``image/png``, or if ``png`` is not
            a PNG within :data:`MAX_EMBEDDING_BYTES`.  Raising rather than
            returning ``None`` keeps the fault visible to a programming error,
            while the one production caller --
            :func:`capture_failure_embedding` -- converts it into the
            no-embedding outcome the plan specifies, so a bad payload still
            cannot change a test result.
    """
    if not isinstance(mime_type, str) or mime_type.strip().lower() != DEFAULT_MIME_TYPE:
        raise ValueError(
            f"an embedding's media type must be {DEFAULT_MIME_TYPE!r}, not {mime_type!r}"
        )
    if not is_png_bytes(png):
        raise ValueError(
            "an embedding's payload must begin with PNG's eight-byte signature"
        )
    if len(png) > MAX_EMBEDDING_BYTES:
        raise ValueError(
            f"an embedding's payload must be at most {MAX_EMBEDDING_BYTES} bytes, "
            f"not {len(png)}"
        )
    embedding: dict[str, str] = {
        # Written from the constant rather than from the argument, so the value
        # that reaches a ``data:`` URI is always a literal of this project's.
        "mime_type": DEFAULT_MIME_TYPE,
        "data": encode_png(png),
    }
    if name is not None:
        embedding["name"] = name
    return embedding


def capture_failure_embedding(
    driver: _TakesScreenshot,
    scenario_name: str | None,
    *,
    scenario_id: str | None = None,
) -> dict[str, str] | None:
    """Capture a failed scenario's screenshot and return it as an embedding.

    This is the single call the scenario-lifecycle hook makes, and it composes
    the whole of ``Hooks.java:14-15``: capture, then attach.  The hook invokes
    it only when the scenario failed and only while the driver is still live,
    then quits the driver -- the order the source fixes and this signature
    keeps possible.

    Args:
        driver: The live session for the failed scenario, not yet quit.
        scenario_name: The failed scenario's name, used as the attachment name.
            ``None`` omits the name, per :func:`build_embedding`.  This is the
            third argument of the Java ``attach`` call and nothing else: it
            reaches the artifact, so it is not a diagnostic field.
        scenario_id: Keyword-only diagnostic identity, distinct from
            ``scenario_name`` and never part of the embedding.  It is passed
            straight to :func:`capture_png` and used in this function's own
            suppression record, so a missing attachment can be attributed to
            the scenario that lost it.

    Returns:
        The embedding mapping on success, or ``None`` if no screenshot could be
        captured or encoded.

    Notes:
        Never raises, and never inspects or mutates scenario state -- whether
        the scenario failed is the caller's test, exactly as it is in the Java
        hook.  A ``None`` return means "no evidence available" and must be
        treated as identical to "no screenshot was taken": the report
        templates render nothing for an empty attachment list, so the caller
        simply appends nothing and the scenario's status is untouched.
    """
    png = capture_png(driver, scenario_id=scenario_id)
    if png is None:
        # Already logged by ``capture_png``, identity included; log once per
        # failure, not twice.
        return None

    try:
        return build_embedding(png, scenario_name)
    except Exception:
        # Encoding a validated ``bytes`` payload does not fail in practice, but
        # the guarantee this function offers its caller is absolute: no
        # exception raised while gathering optional failure evidence may reach
        # the scenario lifecycle and turn a test result into an error.
        fmt, args = _suppression_record(scenario_id)
        logger.exception(fmt, *args)
        return None
