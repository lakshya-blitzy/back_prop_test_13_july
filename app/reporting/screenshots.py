"""Failure-screenshot capture, encoding and embedding.

The port of the screenshot half of ``Hooks.java:11-18``: capture the viewport
as PNG bytes, base64-encode them, and compose the attachment mapping the report
artifacts carry -- bytes, media type and the scenario's name, captured on
failure only, once, before the caller quits the driver.  The Java hook never
ran (``Hooks.java:5`` imports ``org.junit.After``), so the capability is
registered for real under deviation 6 and the mapping's shape derives from
``scenario.attach`` rather than from an observed artifact.

``features/environment.py`` owns the scenario lifecycle and depends on
:func:`capture_png` and :data:`DEFAULT_MIME_TYPE`, composing the attachment
itself through behave's ``context.attach``.  :func:`build_embedding` and
:func:`capture_failure_embedding` compose that mapping in one call instead;
their consumer today is the test suite, and no production path calls either.
What they return is what ``app/reporting/cucumber_json.py`` serialises into a
failed element's ``embeddings`` array and what the lightbox partial renders as
a ``data:`` URI; both read ``mime_type``, ``data`` and ``name``, whose
underscore spelling is the contract rather than a typo.

An attachment is result-controlled data, so :func:`normalize_embedding` is the
only sanctioned way to obtain a renderable one, and one that fails it is
discarded and logged rather than repaired; the lightbox template and
``app/static/js/report.js`` restate its rules, defence in depth for the Flask
viewer's own reading of the merged JSON report.  A screenshot is evidence about
a result and never part of one, so the two capture entry points -
:func:`capture_png` and :func:`capture_failure_embedding` - suppress every
ordinary ``Exception``, log it once with the caller's ``scenario_id`` and
report ``None``, while ``KeyboardInterrupt`` and ``SystemExit`` propagate.  That
suppression is deviation 19 and belongs to capture alone: the pure helpers do
raise, each documenting what and when.  The module logger installs no handler,
so its records reach stderr by level (AAP 0.6).

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

  This rules out the one control a reader may expect to find here and will
  not: the payload is never **re-encoded**.  Decoding a screenshot to a raster
  and writing a fresh PNG from it is a recognised way to neutralise a hostile
  image, and it is unavailable to this project for two independent reasons,
  both of them plan requirements rather than preferences.  It would require an
  imaging distribution, which the bullet above forbids and which the plan's
  dependency inventory does not contain -- every dependency is pinned there
  and none decodes an image.  And it would change the bytes: the source
  attaches exactly what ``getScreenshotAs(OutputType.BYTES)`` returned, so a
  re-encoded attachment is no longer the evidence the scenario produced, and
  the byte-exact passthrough is asserted as a contract in
  ``tests/test_screenshots.py``.

  What replaces it is *rejection* rather than repair, which is why the
  structural validation below is as thorough as it is: a payload this module
  accepts has had its chunk CRCs verified, its image data inflated under a
  bounded budget to exactly the size its header declares, its rows held to
  the filter types the format defines, and its chunk types held to those a
  decoder can act on -- and anything that fails is discarded rather than
  rewritten.  A re-encoder's value is that it makes a malformed image
  harmless; the value here is that a malformed image never reaches a page at
  all.

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
attachment reaches the three writers as result-controlled data: it arrives in
the merged result set, which is assembled from whatever the per-worker event
files contain, and each writer turns it into a ``data:`` URI inside a document
a human then opens.  A media type taken from that data would let a result
choose the media type of an inline document -- ``image/svg+xml`` is an
executable document in a browser -- and a payload accepted on the strength of
its declared media type would let base64 of something that is not an image
through under the name of one.  Neither is acceptable, so this module owns the
one strict test every consumer applies before rendering, and
:func:`normalize_embedding` is the only sanctioned way to obtain a renderable
attachment.  Three layers are established, in this order, each cheaper than
the one after it:

1. **The media type** is **exactly** ``image/png`` -- not a prefix match, not
   a parameterised variant -- and the value that reaches a ``data:`` URI is
   then written from this module's own literal rather than carried over.
2. **The encoding** is canonical standard base64 within
   :data:`MAX_EMBEDDING_BASE64_CHARS`: the standard alphabet, correct padding
   and zero unused pad bits, proven by decoding strictly and re-encoding to a
   byte-identical string.  The length bound is applied *before* the decode, so
   an oversized payload is rejected without being expanded in memory.
3. **The image itself** is a structurally valid PNG within
   :data:`MAX_EMBEDDING_BYTES`, established by :func:`png_defect`: the
   complete eight-byte signature, an ``IHDR`` first chunk of the
   specification's thirteen bytes, non-zero dimensions inside the dimension
   and pixel budgets, a colour-type and bit-depth combination the
   specification defines, a CRC-checked walk of every chunk that stays inside
   the payload, **a type on every chunk that this format defines and this
   validator can account for**, at least one ``IDAT``, a terminal zero-length
   ``IEND`` with **nothing after it**, and compressed image data that inflates
   -- under a bounded, block-at-a-time budget -- to exactly the byte count the
   header's geometry implies **and frames into rows whose filter bytes the
   specification defines**.

Layer 3 is what the eight-byte signature test it replaced could not do.  A
signature followed by arbitrary bytes, a payload truncated mid-chunk, a
corrupted chunk, a polyglot carrying a second document after ``IEND``, a
declared geometry whose decompressed size is orders of magnitude larger than
the payload -- the classic decompression bomb -- image data that inflates to
the right size but frames into rows no decoder can reconstruct, a chunk type
the format does not define presented as one a decoder must understand, and a
chunk carrying a *second* compressed stream outside this validator's
inflation budget are each a PNG to a signature test and each rejected here.
No imaging distribution is involved: the walk is byte arithmetic and ``zlib``,
both of which the standard library supplies, so the plan's
no-imaging-dependency constraint is intact.  Nor is the image ever
*transformed*: a payload either passes unchanged or is discarded.

An attachment failing any layer is **discarded and logged** with the reason,
never repaired and never rendered.  The template and script guards in
``app/templates/partials/lightbox.html`` and ``app/static/js/report.js`` apply
what their surfaces can -- the template cannot decode base64 at all and the
script has no ``zlib`` -- so they are **defence in depth over a strictly
weaker rule**, never the primary control.  Both are held to that relationship
by ``tests/test_png_embedding_contract.py``: whatever this module accepts they
must accept, and whatever they reject this module must reject.

**One bound, shared with the shard schema.**  ``app/reporting/events.py``
refuses to load a worker result file whose embedding ``data`` exceeds
``MAX_EMBEDDING_DATA_LENGTH`` characters of base64.  A producer permitted more
than that could capture a screenshot that makes its own shard unloadable, so
:data:`MAX_EMBEDDING_BASE64_CHARS` **is** that value and
:data:`MAX_EMBEDDING_BYTES` is derived from it -- the largest decoded image
whose canonical encoding still fits.  The two constants are asserted equal in
``tests/test_png_embedding_contract.py``, so the producer and the schema
cannot drift apart silently.
"""

from __future__ import annotations

import base64
import binascii
import logging
import zlib
from collections.abc import Mapping, Sequence
from typing import Any, Final, NamedTuple, Protocol

__all__ = [
    "CAPTURE_FAILURE_MESSAGE",
    "DEFAULT_MIME_TYPE",
    "INVALID_EMBEDDING_MESSAGE",
    "MAX_DECOMPRESSED_BYTES",
    "MAX_EMBEDDING_BASE64_CHARS",
    "MAX_EMBEDDING_BYTES",
    "MAX_IMAGE_DIMENSION",
    "MAX_IMAGE_PIXELS",
    "MAX_PNG_CHUNKS",
    "MIN_PNG_BYTES",
    "PNG_BASE64_HEADER",
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
    "png_defect",
]

# Module logger.  Deliberately left without a handler of its own -- in
# particular without a ``logging.NullHandler`` -- because the plan requires
# capture failures to reach **stderr**.  The process entry point installs the
# handler split that routes WARNING-and-above to stderr; when logging has not
# been configured at all (a bare ``import`` in a test, say), Python's
# ``logging.lastResort`` handler still emits WARNING-and-above to stderr.
# Attaching a NullHandler here would satisfy neither, because it would count as
# a handler and silence both routes.  Nothing in this module writes to
# ``sys.stderr`` directly: stderr routing is achieved by log level.
#
# What a suppression record is allowed to *say* is not this module's to render
# either, and deliberately so.  The records below carry a caller-supplied
# scenario identity -- built from feature text, so quoted with ``%r`` here so
# that it is delimited and cannot break the line -- and, for a capture that
# raised, the driver's own exception with its traceback, which can quote an
# absolute workspace path, a session detail or a substituted step containing
# this suite's fixture account.  Every handler ``app/logging_config.py``
# installs carries its sanitizing formatter, so each of those records is
# relativized, rendered control-safe, redacted and bounded on the way to the
# console, with each traceback line marked so none of it can be read as a
# record of its own.  That is why this module still imports nothing from the
# logging configuration: the protection is at the console boundary, where it
# covers every record rather than the ones an author remembered.
logger = logging.getLogger(__name__)

#: The MIME type the source attaches its screenshot bytes under, verbatim from
#: ``Hooks.java:15``.  Not configurable -- see the module docstring on the
#: absence of an enable flag; the same reasoning forbids a format switch.
DEFAULT_MIME_TYPE: Final[str] = "image/png"

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

    The record's shape is decided here so the three call sites cannot drift
    apart.  Each unpacks the result as ``fmt, args`` and logs
    ``logger.error(fmt, *args)``, which keeps the standard library's lazy
    ``%``-interpolation: a record that is filtered out is never rendered.

    Args:
        scenario_id: The caller's diagnostic identity for the scenario whose
            evidence is missing -- in this port the feature URI, the line and
            the scenario name, supplied by ``features/environment.py``.
            ``None``, the default everywhere, degrades the record to
            :data:`CAPTURE_FAILURE_MESSAGE` alone rather than logging
            ``None``.
        detail_format: An optional extra segment, as a ``%``-format fragment,
            for a failure with something to add beyond the exception the
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

#: The longest encoded attachment payload this project accepts, in base64
#: characters, and the **primary** bound of the pair below it.
#:
#: It is expressed in encoded characters rather than decoded bytes because the
#: binding constraint is a consumer's, not this module's:
#: ``app/reporting/events.py`` refuses to load a per-worker result file whose
#: embedding ``data`` string exceeds its own ``MAX_EMBEDDING_DATA_LENGTH``, and
#: that value is this one.  A producer allowed more would be able to capture a
#: screenshot that makes its own shard unloadable -- the whole shard, not just
#: the attachment -- so the producer's ceiling is the schema's ceiling and
#: nothing else.  ``tests/test_png_embedding_contract.py`` imports both
#: constants and asserts them equal, which is what keeps the agreement a
#: checked fact rather than a comment.
#:
#: Sixteen mebibytes of base64 is roughly twelve of image: far above any
#: viewport screenshot -- a 4K PNG runs to single-digit megabytes, and the
#: 1280x720 Chrome capture this port was measured against is fifteen kilobytes
#: -- and far below a size that threatens a writer, a shard reader or the
#: single-page HTML artifact the payload is inlined into.
MAX_EMBEDDING_BASE64_CHARS: Final[int] = 16 * 1024 * 1024

#: Base64's quantum: four encoded characters carry three bytes, and a
#: canonical payload's length is always a whole number of those four-character
#: groups.  Named because both the bound below and the encoding test in
#: :func:`decode_png_payload` are statements about this quantum rather than
#: about any particular size.
_BASE64_QUANTUM_CHARS: Final[int] = 4
_BASE64_QUANTUM_BYTES: Final[int] = 3

#: The largest decoded attachment this project will render inline, in bytes:
#: the largest image whose canonical encoding still fits in
#: :data:`MAX_EMBEDDING_BASE64_CHARS`.  Dividing by the quantum's characters
#: and multiplying by its bytes is the exact inverse for any payload that is a
#: whole number of quanta -- and the result is one, so no rounding slack is
#: needed and none is taken.  Derived rather than written as a second literal,
#: because the failure this prevents is precisely two independently maintained
#: numbers disagreeing.
MAX_EMBEDDING_BYTES: Final[int] = (
    MAX_EMBEDDING_BASE64_CHARS // _BASE64_QUANTUM_CHARS
) * _BASE64_QUANTUM_BYTES

# --------------------------------------------------------------------------- #
# The PNG format, as much of it as validating an attachment requires
#
# Every constant below is a fact about the format taken from its
# specification, and each is named rather than written into the walk so the
# walk reads as the specification does.  Nothing here is configurable: a PNG
# is a PNG.
# --------------------------------------------------------------------------- #

#: Width of a chunk's length field, of its type field and of its CRC field --
#: four bytes each, big-endian.
_CHUNK_FIELD_BYTES: Final[int] = 4

#: Bytes a chunk costs beyond its body: the length field, the type field and
#: the CRC field.
_CHUNK_OVERHEAD_BYTES: Final[int] = _CHUNK_FIELD_BYTES * 3

#: Length of ``IHDR``'s body, fixed by the specification at thirteen bytes:
#: width, height, bit depth, colour type, compression method, filter method
#: and interlace method.
_IHDR_BODY_BYTES: Final[int] = 13

#: The largest body a chunk may declare.  The length field is four bytes but
#: the specification confines it to 31 bits, so a value with the top bit set
#: is malformed regardless of the payload's size.
_MAX_CHUNK_BODY_BYTES: Final[int] = 0x7FFFFFFF

#: Chunk types this module reasons about.  The rest of the format's chunks are
#: walked, CRC-checked and otherwise left alone: an attachment carrying
#: ``gAMA``, ``pHYs``, ``sRGB``, ``tEXt`` or ``tIME`` is an ordinary
#: screenshot, and rejecting it would discard evidence for no reason.
_IHDR_CHUNK: Final[bytes] = b"IHDR"
_PLTE_CHUNK: Final[bytes] = b"PLTE"
_IDAT_CHUNK: Final[bytes] = b"IDAT"
_IEND_CHUNK: Final[bytes] = b"IEND"

#: The only characters a chunk type may contain.  The specification defines a
#: chunk type as four ASCII letters, whose cases carry the property bits, so a
#: type containing a digit, a space or a high byte is malformed -- and a
#: payload of arbitrary bytes behind a valid signature is rejected here rather
#: than being walked as though its noise were chunk headers.
_CHUNK_TYPE_LETTERS: Final[bytes] = (
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)

#: The chunk types the specification defines as **critical**, which is all
#: four of them.  A critical chunk is one a decoder may not skip, so the
#: specification requires a decoder that does not recognise one to refuse the
#: file -- and this validator recognises exactly these.  An unknown critical
#: chunk is therefore rejected rather than walked past, which is the
#: difference between validating a PNG and validating a chunk container that
#: happens to open like one.
_CRITICAL_CHUNKS: Final[frozenset[bytes]] = frozenset(
    {_IHDR_CHUNK, _PLTE_CHUNK, _IDAT_CHUNK, _IEND_CHUNK}
)

#: The ancillary chunks an attachment may carry: every one of them carries
#: **uncompressed** data, and every one is metadata a real encoder legitimately
#: emits -- the PNGs tracked in this repository carry ``sBIT``, ``pHYs``,
#: ``tEXt``, ``sRGB`` and ``gAMA`` between them, while Chrome and Firefox
#: screenshots carry none at all.
#:
#: Everything else is refused, and the reason is the inflation budget rather
#: than tidiness.  ``iCCP``, ``zTXt`` and a compressed ``iTXt`` each carry a
#: *second* deflate stream, and APNG's ``fdAT`` carries further image frames;
#: this validator budgets the ``IDAT`` stream it inflates and cannot budget
#: theirs, so a payload carrying one would hand a consumer's decoder
#: compressed data nothing here has bounded.  A screenshot needs none of them,
#: so the safe rule is the narrow one.
_ALLOWED_ANCILLARY_CHUNKS: Final[frozenset[bytes]] = frozenset(
    {
        b"tRNS",
        b"cHRM",
        b"gAMA",
        b"sBIT",
        b"sRGB",
        b"bKGD",
        b"hIST",
        b"sPLT",
        b"pHYs",
        b"tIME",
        b"tEXt",
    }
)

#: The position of a chunk type's reserved character, and the case the
#: specification requires of it.  Byte three carries the reserved bit, which
#: must be zero -- an upper-case letter -- in every chunk of a conforming
#: file, so a lower-case third character marks a chunk type from a future
#: version of the format that this validator cannot reason about.
_RESERVED_CHARACTER_INDEX: Final[int] = 2

#: The upper-case letters, for reading a chunk type's property bits.
_UPPER_CASE_LETTERS: Final[bytes] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: The largest palette the format permits, whatever the bit depth: 256
#: entries.  The bit depth bounds it further for indexed colour -- a two-bit
#: image can index four -- but for a suggested palette on truecolour data the
#: depth allows more than the format does, so both bounds are needed and the
#: tighter one wins.
_MAX_PALETTE_ENTRIES: Final[int] = 256

#: The five filter types a scanline may declare, from the specification's
#: filter-method-0 table: none, sub, up, average and Paeth.  A row declaring
#: anything else cannot be reconstructed by any decoder, and a payload whose
#: rows declare such a value is malformed however well-formed its chunks are.
_SCANLINE_FILTER_TYPES: Final[frozenset[int]] = frozenset({0, 1, 2, 3, 4})

#: Bytes per ``PLTE`` entry: one red, one green and one blue sample.
_PLTE_ENTRY_BYTES: Final[int] = 3

#: The colour type whose image data indexes a palette, and which therefore
#: cannot be read without a ``PLTE`` chunk.
_COLOUR_TYPE_INDEXED: Final[int] = 3

#: The colour types a palette may not accompany: greyscale, and greyscale with
#: alpha.  Their samples are intensities, so a palette would be meaningless
#: and the specification forbids it.
_GREYSCALE_COLOUR_TYPES: Final[tuple[int, ...]] = (0, 4)

#: The position, counting from one, at which ``IHDR`` must appear.
_FIRST_CHUNK_POSITION: Final[int] = 1

#: The smallest width or height the format permits: an image has pixels.
_MIN_IMAGE_DIMENSION: Final[int] = 1

#: Samples per pixel for each colour type the specification defines: greyscale,
#: truecolour, indexed, greyscale with alpha, and truecolour with alpha.  A
#: colour type absent from this mapping does not exist.
_CHANNELS_BY_COLOUR_TYPE: Final[dict[int, int]] = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}

#: The bit depths each colour type permits, verbatim from the specification's
#: table.  The pairing matters: depth 2 is legal for greyscale and illegal for
#: truecolour, and a validator that checked the two fields independently would
#: accept a header no decoder can read.
_BIT_DEPTHS_BY_COLOUR_TYPE: Final[dict[int, tuple[int, ...]]] = {
    0: (1, 2, 4, 8, 16),
    2: (8, 16),
    3: (1, 2, 4, 8),
    4: (8, 16),
    6: (8, 16),
}

#: The only compression method and the only filter method the format defines.
_COMPRESSION_METHOD_DEFLATE: Final[int] = 0
_FILTER_METHOD_ADAPTIVE: Final[int] = 0

#: The interlace methods the format defines: none, and Adam7.
_INTERLACE_METHODS: Final[tuple[int, ...]] = (0, 1)
_INTERLACE_ADAM7: Final[int] = 1

#: Adam7's seven passes as ``(x offset, y offset, x step, y step)``.  Needed
#: because an interlaced image's decompressed size is *not* the product of its
#: dimensions: each pass carries its own filter byte per row, so the total
#: exceeds the non-interlaced figure and a single formula would reject every
#: interlaced attachment.
_ADAM7_PASSES: Final[tuple[tuple[int, int, int, int], ...]] = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)

#: Bits in a byte, for the scanline arithmetic below: a row of samples is
#: packed and then rounded up to a whole number of bytes.
_BITS_PER_BYTE: Final[int] = 8

#: Bytes a scanline costs beyond its samples: the one filter-type byte every
#: row of a PNG carries.
_FILTER_BYTE_PER_ROW: Final[int] = 1

#: The smallest byte count a structurally complete PNG can have: the
#: signature, an ``IHDR`` chunk, some ``IDAT`` chunk and an ``IEND`` chunk.
#: A shorter payload cannot be a PNG whatever its first bytes say, and saying
#: so first turns a truncated capture into one clear rejection instead of a
#: walk that runs out of bytes.
MIN_PNG_BYTES: Final[int] = (
    len(PNG_SIGNATURE)
    + (_CHUNK_OVERHEAD_BYTES + _IHDR_BODY_BYTES)
    + _CHUNK_OVERHEAD_BYTES
    + _CHUNK_OVERHEAD_BYTES
)

#: The largest width or height an attachment may declare.  Sixteen thousand
#: three hundred and eighty-four is the maximum dimension Chrome itself will
#: produce, so no real capture approaches it, while a header claiming more --
#: the specification permits up to 2**31-1 -- is refused before any arithmetic
#: is done with it.
MAX_IMAGE_DIMENSION: Final[int] = 16384

#: The largest pixel count an attachment may declare.  Forty million is above
#: every screenshot this port can take (an 8K frame is thirty-three million,
#: and a 1920-wide capture would have to be twenty thousand pixels tall to
#: reach it) and is the first line of defence against a decompression bomb:
#: the geometry is refused before a single byte is inflated.
MAX_IMAGE_PIXELS: Final[int] = 40_000_000

#: The largest decompressed image this module will inflate, in bytes.  The
#: pixel budget bounds the *area*; this bounds the actual raw byte count, which
#: also depends on the channel count and the bit depth -- a forty-megapixel
#: sixteen-bit RGBA image would be three hundred and twenty megabytes and is
#: refused here even though its area passes.  Checked against the geometry
#: before inflating, and again as a running total while inflating.
MAX_DECOMPRESSED_BYTES: Final[int] = 256 * 1024 * 1024

#: The most chunks an attachment may carry.  Encoders split image data across
#: several ``IDAT`` chunks -- Chrome emits them at four kilobytes each, so a
#: payload at :data:`MAX_EMBEDDING_BYTES` arrives in roughly three thousand --
#: and this leaves an order of magnitude above that while still bounding a
#: payload made entirely of empty chunks.
MAX_PNG_CHUNKS: Final[int] = 65536

#: How much decompressed data is held at once while the image data is verified.
#: The stream is inflated a block at a time and each block is counted and
#: discarded, so peak memory is this value rather than the whole decompressed
#: image -- which is what lets :data:`MAX_DECOMPRESSED_BYTES` be generous
#: without ever allocating it.
_INFLATE_BLOCK_BYTES: Final[int] = 1024 * 1024

#: The fixed base64 prefix of the PNG signature, for the one guard that cannot
#: decode at all: a Jinja template.  Base64 maps each three bytes onto four
#: characters, so characters 1-8 are fixed by signature bytes 1-6 and
#: characters 9-11 carry bytes 7 and 8 together with the top two bits of byte
#: 9.  Byte 9 is the high byte of the length of a PNG's first chunk, which is
#: always IHDR at 13 bytes, so those two bits are always zero and the eleventh
#: character is always ``o``.  This prefix therefore establishes the complete
#: eight-byte signature and rejects nothing a real PNG produces.  Asserted
#: against genuine PNGs in the contract tests rather than trusted as
#: arithmetic.
PNG_BASE64_SIGNATURE: Final[str] = "iVBORw0KGgo"

#: The fixed base64 prefix of a PNG's whole opening **header**, and what the
#: template guard actually requires.
#:
#: Every valid PNG begins with the same sixteen bytes, not merely the same
#: eight: the signature, then the length of the first chunk, which the
#: specification fixes at ``IHDR``'s thirteen bytes, then the four characters
#: ``IHDR`` itself.  Fifteen of those sixteen bytes encode as exactly twenty
#: base64 characters with no bits of a later byte mixed in, so those twenty
#: characters are identical in every PNG that exists and a payload not opening
#: with them cannot be one.
#:
#: This is what makes the template's guard more than a signature test.  The
#: signature-only payload ``iVBORw0KGgo=`` -- eleven characters of PNG
#: signature and nothing else -- satisfies :data:`PNG_BASE64_SIGNATURE` and
#: would have become an image ``data:`` URI; it cannot satisfy this, because
#: it has no header after its signature.  Asserted against genuine PNGs,
#: including a real browser capture, in the contract tests.
PNG_BASE64_HEADER: Final[str] = "iVBORw0KGgoAAAANSUhE"


def _has_png_signature(data: bytes) -> bool:
    """Whether ``data`` opens with PNG's complete eight-byte signature.

    The project's one spelling of the signature test, kept separate from
    :func:`png_defect` so that the cheap prefix question -- which the capture
    path asks about a driver's return value, and which
    :func:`decode_png_payload` asks before doing anything expensive -- has a
    single definition and is not restated as an index comparison anywhere.

    Args:
        data: The bytes to inspect.

    Returns:
        ``True`` when the first eight bytes are :data:`PNG_SIGNATURE`.
        Checking eight rather than six matters: the first six alone are also
        the first six bytes of a payload whose seventh and eighth are
        anything at all, so a six-byte test accepts base64 that is not a PNG.
    """
    return data.startswith(PNG_SIGNATURE)


def _scanline_length(width: int, channels: int, bit_depth: int) -> int:
    """Bytes one scanline occupies raw, its filter-type byte included.

    Args:
        width: Pixels per row in this image or Adam7 pass.
        channels: Samples per pixel, from :data:`_CHANNELS_BY_COLOUR_TYPE`.
        bit_depth: Bits per sample, from the header.

    Returns:
        The row's samples packed into whole bytes, rounded up, plus the one
        filter-type byte every PNG row carries.
    """
    row_samples = width * channels * bit_depth
    packed = (row_samples + _BITS_PER_BYTE - 1) // _BITS_PER_BYTE
    return packed + _FILTER_BYTE_PER_ROW


def _scanline_layout(
    width: int,
    height: int,
    channels: int,
    bit_depth: int,
    interlace: int,
) -> tuple[tuple[int, int], ...]:
    """The exact row layout the header's geometry implies.

    The layout is what makes the image data *checkable* rather than merely
    countable: it says where every row begins, and therefore where every
    filter-type byte is, so the inflated stream can be held to the
    specification's row framing instead of only to its total size.

    Args:
        width: Image width in pixels.
        height: Image height in pixels.
        channels: Samples per pixel.
        bit_depth: Bits per sample.
        interlace: The header's interlace method -- ``0`` for none, ``1`` for
            Adam7.

    Returns:
        One ``(row length, row count)`` pair per segment of the stream, in
        stream order, omitting empty segments.  A progressive image has one
        segment; an Adam7 image has one per non-empty pass of
        :data:`_ADAM7_PASSES`, each with its own row length and therefore its
        own filter bytes -- which is why an interlaced image occupies *more*
        raw than the same image stored progressively, and why a validator that
        assumed one row length would reject every interlaced attachment.
    """
    if interlace != _INTERLACE_ADAM7:
        if width <= 0 or height <= 0:
            return ()
        return ((_scanline_length(width, channels, bit_depth), height),)

    layout: list[tuple[int, int]] = []
    for x_offset, y_offset, x_step, y_step in _ADAM7_PASSES:
        pass_width = (width - x_offset + x_step - 1) // x_step if width > x_offset else 0
        pass_height = (
            (height - y_offset + y_step - 1) // y_step if height > y_offset else 0
        )
        if pass_width <= 0 or pass_height <= 0:
            continue
        layout.append((_scanline_length(pass_width, channels, bit_depth), pass_height))
    return tuple(layout)


def _decompressed_bytes(layout: tuple[tuple[int, int], ...]) -> int:
    """The exact decompressed size a row layout implies.

    Exact rather than approximate, because it is compared for equality with
    what the image data actually inflates to: an image that inflates to a
    different size than its own header describes is malformed whichever way
    the difference falls, and an inequality would accept both halves of that.

    Args:
        layout: The layout from :func:`_scanline_layout`.

    Returns:
        The total decompressed byte count.
    """
    return sum(length * count for length, count in layout)


class _ScanlineWalker:
    """Checks a row layout's filter-type bytes as the stream arrives.

    The image data is inflated a block at a time and each block is discarded,
    so the framing has to be checked *while* the bytes go past: this walker
    keeps the position within the current row and the rows left in the current
    segment, and inspects exactly those bytes that a row begins with.  Nothing
    is retained -- no scanline, no pixel and no copy of a block -- so a large
    image costs one block of memory however it is filtered.

    Checking the filter byte is what makes this a decode rather than a byte
    count.  A row declaring filter type 5 or 200 cannot be reconstructed by
    any decoder: the file is malformed, and the only reason it survived a
    signature test, a CRC walk and an exact size comparison is that none of
    those three look inside the decompressed data at all.
    """

    def __init__(self, layout: tuple[tuple[int, int], ...]) -> None:
        """Begin at the first row of the first segment.

        :param layout: ``(row length, row count)`` pairs in stream order.
        """
        self._layout = layout
        self._segment = 0
        self._rows_left = layout[0][1] if layout else 0
        self._row_remaining = 0

    def feed(self, block: bytes) -> str | None:
        """Walk one inflated block, checking any row it starts.

        :param block: The next stretch of decompressed image data.
        :returns: ``None`` while the framing holds, and a short reason for the
            first row whose filter type the format does not define.
        """
        at = 0
        while at < len(block):
            if self._row_remaining:
                step = min(self._row_remaining, len(block) - at)
                self._row_remaining -= step
                at += step
                continue
            if self._rows_left <= 0:
                # More data than the layout accounts for.  The size comparison
                # in :func:`_image_data_defect` reports this, and stopping here
                # keeps the walk from indexing past its own layout.
                return None
            filter_type = block[at]
            if filter_type not in _SCANLINE_FILTER_TYPES:
                return (
                    f"a scanline declares filter type {filter_type}, which the "
                    "format does not define"
                )
            length, _ = self._layout[self._segment]
            self._row_remaining = length - _FILTER_BYTE_PER_ROW
            self._rows_left -= 1
            if self._rows_left <= 0 and self._segment + 1 < len(self._layout):
                self._segment += 1
                self._rows_left = self._layout[self._segment][1]
            at += 1
        return None

    @property
    def complete(self) -> bool:
        """Whether every row of every segment was seen in full."""
        return (
            self._row_remaining == 0
            and self._rows_left <= 0
            and self._segment + 1 >= len(self._layout)
        )


class _ImageHeader(NamedTuple):
    """The four facts the chunk walk needs from a validated ``IHDR``.

    :ivar colour_type: The header's colour type, kept because the palette
        rules later in the walk depend on it.
    :ivar bit_depth: The header's bit depth, kept for the same reason: it
        bounds how many palette entries can be indexed.
    :ivar decompressed_bytes: The exact size the image data must inflate to.
    :ivar layout: The row layout that size is the sum of, so the image data
        can be checked row by row and not only in total.
    """

    colour_type: int
    bit_depth: int
    decompressed_bytes: int
    layout: tuple[tuple[int, int], ...]


def _header_defect(body: bytes) -> tuple[str | None, _ImageHeader | None]:
    """Validate an ``IHDR`` body and answer what the walk needs from it.

    Args:
        body: The thirteen bytes of the ``IHDR`` chunk, already CRC-checked by
            the walk.

    Returns:
        A ``(defect, header)`` pair with exactly one member set.  ``defect`` is
        ``None`` and ``header`` an :class:`_ImageHeader` when the header is one
        the specification defines and within this module's budgets; otherwise
        ``defect`` is a short reason and ``header`` is ``None``.
    """
    width = int.from_bytes(body[:_CHUNK_FIELD_BYTES], "big")
    height = int.from_bytes(body[_CHUNK_FIELD_BYTES : _CHUNK_FIELD_BYTES * 2], "big")
    bit_depth, colour_type, compression, filter_method, interlace = body[
        _CHUNK_FIELD_BYTES * 2 :
    ]

    # Zero is the case a bounds check alone misses: it is inside every upper
    # bound, it is what a truncated or zeroed header reads as, and an image
    # with no pixels is not a screenshot of anything.
    if width < _MIN_IMAGE_DIMENSION or height < _MIN_IMAGE_DIMENSION:
        return "the header declares a zero width or height", None
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        return (
            (
                f"the header declares {width}x{height}, beyond the "
                f"{MAX_IMAGE_DIMENSION}-pixel dimension limit"
            ),
            None,
        )
    if width * height > MAX_IMAGE_PIXELS:
        return (
            (
                f"the header declares {width * height} pixels, beyond the "
                f"{MAX_IMAGE_PIXELS}-pixel limit"
            ),
            None,
        )
    if colour_type not in _CHANNELS_BY_COLOUR_TYPE:
        return f"colour type {colour_type} is not one the format defines", None
    if bit_depth not in _BIT_DEPTHS_BY_COLOUR_TYPE[colour_type]:
        return (
            f"bit depth {bit_depth} is not permitted for colour type {colour_type}",
            None,
        )
    if compression != _COMPRESSION_METHOD_DEFLATE:
        return f"compression method {compression} is not one the format defines", None
    if filter_method != _FILTER_METHOD_ADAPTIVE:
        return f"filter method {filter_method} is not one the format defines", None
    if interlace not in _INTERLACE_METHODS:
        return f"interlace method {interlace} is not one the format defines", None

    layout = _scanline_layout(
        width,
        height,
        _CHANNELS_BY_COLOUR_TYPE[colour_type],
        bit_depth,
        interlace,
    )
    decompressed = _decompressed_bytes(layout)
    # The decompression-bomb test, made before a byte is inflated: the header
    # says how large the image becomes, and a payload whose declared expansion
    # exceeds the budget is refused on that statement alone.
    if decompressed > MAX_DECOMPRESSED_BYTES:
        return (
            (
                f"the header implies {decompressed} decompressed bytes, beyond "
                f"the {MAX_DECOMPRESSED_BYTES}-byte limit"
            ),
            None,
        )
    return None, _ImageHeader(colour_type, bit_depth, decompressed, layout)


def _palette_defect(
    length: int,
    header: _ImageHeader | None,
    saw_palette: bool,
    saw_image_data: bool,
) -> str | None:
    """Validate a ``PLTE`` chunk against the header and its position.

    Args:
        length: The chunk's declared body length, already proven to fit inside
            the payload by the walk.
        header: The validated ``IHDR`` facts.  ``None`` cannot occur -- the
            walk rejects a payload whose first chunk is not ``IHDR`` -- and is
            reported rather than assumed away, so this function is total.
        saw_palette: Whether a ``PLTE`` chunk has already been seen.
        saw_image_data: Whether any ``IDAT`` chunk has already been seen.

    Returns:
        ``None`` when the palette is one the specification permits here, and a
        short reason otherwise.
    """
    if header is None:
        return "PLTE appears before IHDR"
    if saw_palette:
        return "the payload carries more than one PLTE chunk"
    if saw_image_data:
        return "PLTE appears after the image data"
    if header.colour_type in _GREYSCALE_COLOUR_TYPES:
        return f"PLTE is not permitted for colour type {header.colour_type}"
    if length % _PLTE_ENTRY_BYTES:
        return (
            f"PLTE carries {length} bytes, not a whole number of "
            f"{_PLTE_ENTRY_BYTES}-byte entries"
        )
    entries = length // _PLTE_ENTRY_BYTES
    # Two bounds, and the tighter one governs: the format caps a palette at
    # 256 entries whatever the image is, and for indexed colour the bit depth
    # caps it further.  Checking only the depth accepts a 257-entry suggested
    # palette on sixteen-bit truecolour, where the depth allows 65536 and the
    # format allows 256.
    permitted = min(_MAX_PALETTE_ENTRIES, 1 << header.bit_depth)
    if not entries or entries > permitted:
        return (
            f"PLTE declares {entries} entries where the format and bit depth "
            f"{header.bit_depth} permit {permitted}"
        )
    return None


def _chunk_type_defect(chunk_type: bytes) -> str | None:
    """Whether this chunk type is one an attachment may carry.

    Three rules, and each rejects something a permissive walk lets through:

    * **A critical chunk must be one of the four the format defines.**  The
      specification requires a decoder that meets an unrecognised critical
      chunk to refuse the file, precisely because a critical chunk changes how
      the image is to be read.  Walking past one -- CRC-checking it and
      ignoring it -- validates a chunk container rather than a PNG.
    * **The reserved bit must be zero**, which is to say the third character
      must be upper case.  A lower-case third character marks a chunk from a
      future version of the format whose meaning this validator cannot know.
    * **An ancillary chunk must be one of the safe, uncompressed set.**  See
      :data:`_ALLOWED_ANCILLARY_CHUNKS` for why the rule is an allowlist: the
      chunks it excludes carry a second compressed stream, or further image
      frames, that this validator's inflation budget does not cover.

    Args:
        chunk_type: The four-letter type, already proven to be letters.

    Returns:
        ``None`` for a type an attachment may carry, and a short reason
        otherwise.
    """
    name = chunk_type.decode("ascii")
    if chunk_type[_RESERVED_CHARACTER_INDEX] not in _UPPER_CASE_LETTERS:
        return f"the {name} chunk's reserved bit is not zero"
    if chunk_type[0] in _UPPER_CASE_LETTERS:
        if chunk_type not in _CRITICAL_CHUNKS:
            return f"{name} is a critical chunk this format does not define"
        return None
    if chunk_type not in _ALLOWED_ANCILLARY_CHUNKS:
        return f"the {name} chunk is not one an attachment may carry"
    return None


def _image_data_defect(
    bodies: list[bytes],
    expected: int,
    layout: tuple[tuple[int, int], ...],
) -> str | None:
    """Verify the image data inflates to exactly the declared size.

    This is the strict-decode half of the contract, and the reason a corrupted
    or synthetic payload cannot pass: the concatenated ``IDAT`` bodies are one
    zlib stream, and a stream that will not inflate, that ends early, that
    carries trailing data, or that produces a different number of bytes than
    the header's geometry calls for is not an image a decoder can read.

    Memory is bounded rather than trusted.  The stream is inflated
    :data:`_INFLATE_BLOCK_BYTES` at a time and every block is counted and then
    dropped, so a payload whose declared geometry is large costs time
    proportional to that geometry but never holds more than one block --
    which is what makes the running total a real defence and not an
    allocation of its own.

    What the stream inflates *to* is checked as well as how much of it there
    is: :class:`_ScanlineWalker` reads the filter-type byte every row begins
    with, in the row layout the header implies, as the blocks go past.  A
    payload whose rows declare an undefined filter type is malformed and no
    decoder can reconstruct it, and neither the CRC walk nor the size
    comparison can see that, because neither looks inside the decompressed
    data.

    Args:
        bodies: Every ``IDAT`` body, in file order.
        expected: The byte count :func:`_header_defect` derived from the
            header.
        layout: The row layout that byte count is the sum of.

    Returns:
        ``None`` when the stream inflates to exactly ``expected`` bytes, frames
        into the layout's rows with defined filter types, and ends cleanly; a
        short reason otherwise.
    """
    inflater = zlib.decompressobj()
    walker = _ScanlineWalker(layout)
    produced = 0
    try:
        # The empty body appended to the chunk list is the final drain: with a
        # block bound set, the decompressor returns unprocessed *input* in
        # ``unconsumed_tail``, which the inner loop feeds back, and one last
        # empty feed collects anything it still holds internally.  Appending it
        # rather than writing a second loop keeps the running total and the
        # block bound stated once, which is what they have to be to be a
        # guarantee.
        for body in (*bodies, b""):
            pending = body
            while True:
                block = inflater.decompress(pending, _INFLATE_BLOCK_BYTES)
                produced += len(block)
                if produced > expected:
                    return (
                        "the image data inflates beyond the size its header "
                        "declares"
                    )
                defect = walker.feed(block)
                if defect is not None:
                    return defect
                pending = inflater.unconsumed_tail
                if not block and not pending:
                    break
    except zlib.error:
        # A stream that will not inflate, or whose checksum fails, is a
        # rejected attachment rather than an exception: the reason is reported
        # and no detail of the payload is quoted.
        return "the image data is not a readable compressed stream"

    if not inflater.eof:
        return "the image data ends before its compressed stream does"
    if inflater.unused_data:
        return "the image data carries bytes after its compressed stream"
    if produced != expected:
        return (
            f"the image data inflates to {produced} bytes where its header "
            f"declares {expected}"
        )
    if not walker.complete:
        # Unreachable while the size comparison above holds -- the layout's
        # rows sum to exactly ``expected`` -- and kept because the framing
        # guarantee is the walker's, so it is the walker that must say the
        # framing was seen through to the end.
        return "the image data does not fill the rows its header declares"
    return None


def png_defect(data: object) -> str | None:  # noqa: C901,PLR0911,PLR0912,RUF100
    """Validate a PNG payload, answering the first defect found or ``None``.

    The project's single definition of "is a PNG", and deliberately more than
    a signature test.  A signature test answers "these eight bytes look like
    an image", which is the question an attacker is asking too: the module
    docstring lists what a signature-only rule admits, and every clause below
    exists to reject one of those things.  Structure is *established*, not
    assumed:

    * the payload is ``bytes``, is at least :data:`MIN_PNG_BYTES` long and is
      within :data:`MAX_EMBEDDING_BYTES`;
    * it opens with the complete eight-byte signature;
    * every chunk is walked in order -- a four-byte length the specification
      confines to 31 bits and that must fit inside what remains of the
      payload, a four-byte type of ASCII letters, a body, and a CRC that must
      equal the CRC-32 of the type and body together -- with the chunk count
      bounded by :data:`MAX_PNG_CHUNKS`;
    * every chunk's *type* passes :func:`_chunk_type_defect`: a critical
      chunk must be one of the four this format defines, the reserved bit
      must be clear, and an ancillary chunk must be one of the uncompressed
      ones an attachment has any business carrying;
    * the first chunk is ``IHDR``, exactly once, with the specification's
      thirteen-byte body, and the geometry it declares passes
      :func:`_header_defect`;
    * ``PLTE``, when present, precedes the image data, appears once, is a
      whole number of three-byte entries, and declares no more entries than
      the bit depth can index and no more than the format's own ceiling of
      :data:`_MAX_PALETTE_ENTRIES` -- and it is *required* for indexed
      colour, whose image data is meaningless without it;
    * at least one ``IDAT`` is present and the ``IDAT`` chunks are
      consecutive, as the specification requires;
    * the last chunk is a zero-length ``IEND`` and **nothing follows it**,
      which is what rejects a polyglot carrying a second document behind a
      valid image;
    * the image data inflates to exactly the size the header declares *and*
      frames into rows each beginning with a filter type the specification
      defines, per :func:`_image_data_defect`.

    Args:
        data: Any object.  Only ``bytes`` can qualify; a ``bytearray`` or a
            ``memoryview`` is reported rather than coerced, because the
            callers here have already normalised their payload and a silent
            coercion would hide the one that had not.

    Returns:
        ``None`` when the payload is a PNG this project will render, and a
        short human-readable reason otherwise.  The reason names the defect
        and, where a limit was exceeded, the two numbers involved; it never
        quotes payload bytes, because it is written to a log.

    Notes:
        Never raises.  Every malformed payload is a return value, so neither a
        report write nor a scenario's result can be disturbed by one.  No
        imaging distribution is used and none is needed: the walk is byte
        arithmetic and the standard library's ``zlib``.

    Examples:
        >>> png_defect(b"not bytes at all")
        'the payload is 16 bytes, shorter than the 57 a PNG needs'
        >>> png_defect(PNG_SIGNATURE) is None
        False
    """
    if not isinstance(data, bytes):
        return f"the payload is {type(data).__name__}, not bytes"
    if len(data) < MIN_PNG_BYTES:
        return (
            f"the payload is {len(data)} bytes, shorter than the "
            f"{MIN_PNG_BYTES} a PNG needs"
        )
    if len(data) > MAX_EMBEDDING_BYTES:
        return (
            f"the payload is {len(data)} bytes, above the "
            f"{MAX_EMBEDDING_BYTES}-byte limit"
        )
    if not _has_png_signature(data):
        return "the payload does not begin with PNG's eight-byte signature"

    offset = len(PNG_SIGNATURE)
    chunks = 0
    header: _ImageHeader | None = None
    image_data: list[bytes] = []
    saw_palette = False
    saw_end = False
    image_data_closed = False
    previous_was_image_data = False

    while offset < len(data):
        if saw_end:
            return "the payload carries bytes after its IEND chunk"
        if len(data) - offset < _CHUNK_OVERHEAD_BYTES:
            return "the payload ends inside a chunk header"

        length = int.from_bytes(data[offset : offset + _CHUNK_FIELD_BYTES], "big")
        chunk_type = data[offset + _CHUNK_FIELD_BYTES : offset + _CHUNK_FIELD_BYTES * 2]
        if length > _MAX_CHUNK_BODY_BYTES:
            return "a chunk declares a length the format does not permit"
        if length > len(data) - offset - _CHUNK_OVERHEAD_BYTES:
            return "a chunk declares a length that overruns the payload"
        if not all(byte in _CHUNK_TYPE_LETTERS for byte in chunk_type):
            return "a chunk type is not four ASCII letters"
        defect = _chunk_type_defect(chunk_type)
        if defect is not None:
            return defect

        body_at = offset + _CHUNK_FIELD_BYTES * 2
        body = data[body_at : body_at + length]
        stored_crc = int.from_bytes(
            data[body_at + length : body_at + length + _CHUNK_FIELD_BYTES], "big"
        )
        # The CRC covers the type and the body together.  Computed
        # incrementally -- the type's CRC seeding the body's -- rather than
        # over a concatenation, so a multi-megabyte chunk is not copied to be
        # checksummed.
        if zlib.crc32(body, zlib.crc32(chunk_type)) != stored_crc:
            return f"the {chunk_type.decode('ascii')} chunk's CRC does not match it"

        chunks += 1
        if chunks > MAX_PNG_CHUNKS:
            return f"the payload carries more than {MAX_PNG_CHUNKS} chunks"

        if chunks == _FIRST_CHUNK_POSITION and chunk_type != _IHDR_CHUNK:
            return "the payload's first chunk is not IHDR"

        if chunk_type == _IHDR_CHUNK:
            if chunks != _FIRST_CHUNK_POSITION:
                return "IHDR appears more than once"
            if length != _IHDR_BODY_BYTES:
                return (
                    f"IHDR carries {length} bytes where the format fixes "
                    f"{_IHDR_BODY_BYTES}"
                )
            defect, header = _header_defect(body)
            if defect is not None:
                return defect
        elif chunk_type == _PLTE_CHUNK:
            defect = _palette_defect(length, header, saw_palette, bool(image_data))
            if defect is not None:
                return defect
            saw_palette = True
        elif chunk_type == _IDAT_CHUNK:
            if image_data_closed:
                return "the IDAT chunks are not consecutive"
            image_data.append(body)
        elif chunk_type == _IEND_CHUNK:
            if length:
                return "IEND carries a body where the format requires none"
            saw_end = True

        # An IDAT run is closed by the first chunk of another type, so a later
        # IDAT is reported rather than silently appended to the stream.
        if previous_was_image_data and chunk_type != _IDAT_CHUNK:
            image_data_closed = True
        previous_was_image_data = chunk_type == _IDAT_CHUNK

        offset = body_at + length + _CHUNK_FIELD_BYTES

    # Unreachable by construction -- a payload long enough to enter the walk
    # whose first chunk is not IHDR has already been refused above -- and kept
    # because it is what makes the header a value rather than an assumption
    # for the two clauses below, including for a type checker.
    if header is None:
        return "the payload has no IHDR chunk"
    if not saw_end:
        return "the payload has no IEND chunk"
    if not image_data:
        return "the payload has no IDAT chunk"
    if header.colour_type == _COLOUR_TYPE_INDEXED and not saw_palette:
        return "indexed colour without the PLTE chunk it indexes"
    return _image_data_defect(image_data, header.decompressed_bytes, header.layout)


def is_png_bytes(data: object) -> bool:
    """Whether ``data`` is a PNG this project will accept and render.

    The predicate form of :func:`png_defect`, kept because that is how every
    call site here and in ``features/environment.py``'s neighbourhood reads --
    and kept as the *same* name the weaker signature test had, so that every
    existing consumer is strengthened by this module rather than having to be
    changed to ask a new question.

    Args:
        data: Any object.

    Returns:
        ``True`` only when :func:`png_defect` finds nothing wrong: a
        structurally valid, CRC-consistent, in-budget PNG whose image data
        inflates to the size its own header declares.  A signature followed by
        arbitrary bytes answers ``False``, which is the whole point of it.
    """
    return png_defect(data) is None


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
    * **The image.**  The decoded bytes must be a PNG by :func:`png_defect`'s
      whole rule, not merely by their first eight.  Checking six bytes accepts
      ``iVBORw0KAAAA``; checking eight accepts ``iVBORw0KGgo=``, the signature
      alone; checking the structure accepts neither.

    Args:
        payload: The attachment's ``data`` value as it arrived in the result
            set.  Any type is accepted; a non-``str`` is rejected.

    Returns:
        The decoded PNG bytes when every clause holds, and ``None`` otherwise.
        Never raises: a decoding error is a rejection, not an exception, so a
        malformed attachment cannot break a report write.

    Examples:
        >>> decode_png_payload("iVBORw0KGgo=") is None    # the signature alone
        True
        >>> decode_png_payload("iVBORw0KAAAA") is None    # bytes 7-8 wrong
        True
        >>> decode_png_payload("iVBORw0KAB==") is None    # non-zero pad bits
        True
        >>> decode_png_payload("<svg/>") is None
        True
    """
    decoded, _ = _decoded_payload(payload)
    return decoded


def _decoded_payload(payload: object) -> tuple[bytes | None, str | None]:
    """Decode and validate one payload, answering the bytes or the reason.

    The implementation behind :func:`decode_png_payload`, split out only
    because two callers need different halves of the same work: the public
    function wants the bytes, and :func:`normalize_embedding` wants the reason
    so that a discarded attachment says *why* in the log an operator reads.
    Splitting it keeps one implementation of the clauses rather than a
    predicate and a diagnostic that could disagree.

    Args:
        payload: The attachment's ``data`` value.

    Returns:
        A ``(decoded, defect)`` pair with exactly one member set.
    """
    if not isinstance(payload, str):
        return None, f"the payload is {type(payload).__name__}, not text"
    if not payload or len(payload) % _BASE64_QUANTUM_CHARS:
        return None, (
            f"the payload is {len(payload)} characters, not a positive multiple "
            f"of base64's {_BASE64_QUANTUM_CHARS}"
        )
    if len(payload) > MAX_EMBEDDING_BASE64_CHARS:
        return None, (
            f"the payload is {len(payload)} characters, above the "
            f"{MAX_EMBEDDING_BASE64_CHARS}-character limit"
        )

    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        # binascii.Error is a ValueError subclass; both are named so the intent
        # survives a future change to either. Every other exception type would
        # be a programming error and is deliberately not swallowed.
        return None, "the payload is not strict standard base64"

    # Canonicality, and with it the padding-bit test no character scan can make.
    if base64.b64encode(decoded).decode("ascii") != payload:
        return None, "the payload is not the canonical base64 of its own bytes"

    defect = png_defect(decoded)
    if defect is not None:
        return None, defect
    return decoded, None


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

    The media type is **not** carried over from the input: it is compared
    against :data:`DEFAULT_MIME_TYPE` and then written from that constant, so
    the value reaching a ``data:`` URI is always a literal of this project's
    and can never be chosen by a result.

    Args:
        embedding: A candidate attachment mapping, normally with the keys
            ``mime_type``, ``data`` and optionally ``name``.  Any object is
            accepted; anything that is not a mapping is rejected.

    Returns:
        A new mapping carrying the fixed ``mime_type``, the canonical
        ``data``, and ``name`` when the input carried one, or ``None`` when
        the attachment is not a renderable PNG.  The name is carried verbatim
        -- escaping belongs to the serialising consumer -- but a non-string
        name is dropped rather than coerced, so no value can surface in a page
        as the text ``None``.

    Notes:
        A rejection is logged once at warning level, which the CLI's handler
        split routes to stderr, and the caller simply has one fewer
        attachment.
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

    decoded, defect = _decoded_payload(embedding.get("data"))
    if decoded is None:
        # The reason, not just the verdict: an operator reading a CI log needs
        # to know whether the attachment was oversized, truncated, corrupted or
        # never an image, and the reason names that without quoting a byte of
        # the payload itself.
        logger.warning("%s (%s)", INVALID_EMBEDDING_MESSAGE, defect)
        return None

    # Re-encoded from the decoded bytes rather than copied from the input, so
    # the emitted value's canonicality is a property of this function instead
    # of a property of its caller's input.
    payload = encode_png(decoded)
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

    A sequence-level convenience wrapper over
    :func:`normalize_feature_attachments`, for a caller holding a list of
    features.  **No writer calls it today**: the production path is the
    element-level :func:`normalize_element_attachments`, which
    ``app/reporting/aggregation.py`` calls while building each normalized
    element, so the features both HTML writers render arrive already
    validated.

    Args:
        features: The features to validate.  A string, a mapping and ``None``
            are each treated as no features.

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

    The port of ``((TakesScreenshot) Driver.getDriver())``
    ``.getScreenshotAs(OutputType.BYTES)`` at ``Hooks.java:14``.  The driver
    is a parameter rather than a lookup, so the caller that owns the scenario
    lifecycle can guarantee the session is still live.

    Args:
        driver: Any object exposing ``get_screenshot_as_png()``; in production
            the live session for the failed scenario, before it is quit.
        scenario_id: Keyword-only diagnostic identity, reaching whatever
            suppression record this call emits and nothing else.  Omitting it
            leaves the record without an identity, never reading ``None``.

    Returns:
        The PNG bytes, or ``None`` when no usable image could be obtained -- a
        dead session, a driver without the method, or a payload that is not a
        PNG within :data:`MAX_EMBEDDING_BYTES`.

    Notes:
        Every ordinary ``Exception`` is suppressed, logged once at error level
        with ``scenario_id``, and reported as ``None``; ``KeyboardInterrupt``
        and ``SystemExit`` are not caught and still end the run.  That is
        deviation 19: a screenshot failure must not change a test outcome.
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

    # The payload must be what this function's name promises, and the whole of
    # the embedding contract is applied to it -- not a signature test.  A
    # driver that answered a JPEG, an error page, a truncated file or an image
    # whose data no longer decodes would otherwise be embedded under the media
    # type ``image/png``, which is the one thing the contract forbids; the
    # size and geometry bounds are applied for the same reason they are
    # applied to a result-controlled payload, since this image is inlined into
    # a single document a human opens.
    defect = png_defect(png)
    if defect is not None:
        fmt, args = _suppression_record(
            scenario_id,
            "driver returned an unusable screenshot: %s",
            defect,
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
    """Compose the embedding mapping the JSON writer and the lightbox read.

    The port of ``scenario.attach(screenshot, "image/png",
    scenario.getName())`` at ``Hooks.java:15``, shaped as the JSON generator's
    ``createEmbeddingMap`` does.  No production path calls it -- the hook
    attaches raw bytes through behave -- so its consumer is the test suite.

    Args:
        png: The raw PNG bytes to embed.
        name: The attachment's name, the scenario's name in this port;
            ``None`` omits the key rather than emitting ``null``, as the
            generator's ``if (name != null)`` branch does.
        mime_type: Defaults to :data:`DEFAULT_MIME_TYPE`; per-attachment in
            the generator, not configurable here, so any other value is
            rejected.

    Returns:
        ``mime_type`` and ``data``, plus ``name`` when supplied, verbatim.

    Raises:
        TypeError: Propagated from :func:`encode_png` if ``png`` is not
            bytes-like.
        ValueError: If ``mime_type`` is not ``image/png``, or if ``png`` is not
            a valid PNG within :data:`MAX_EMBEDDING_BYTES` by
            :func:`png_defect`'s whole rule -- the message carries the defect
            it reported.  Raising rather than
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
    defect = png_defect(png)
    if defect is not None:
        raise ValueError(f"an embedding's payload must be a valid PNG: {defect}")
    embedding: dict[str, str] = {
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

    :func:`capture_png` followed by :func:`build_embedding` -- the whole of
    ``Hooks.java:14-15`` in one call.  Not on the production path:
    ``features/environment.py`` calls :func:`capture_png` and attaches the
    bytes through behave itself, so the test suite is the consumer.  Call it
    only for a failed scenario, only while the driver is live, then quit it.

    Args:
        driver: The live session for the failed scenario, not yet quit.
        scenario_name: The attachment's name, the Java ``attach`` call's third
            argument; it reaches the artifact, so it is not diagnostic.
        scenario_id: Keyword-only diagnostic identity, never part of the
            embedding, passed to :func:`capture_png` and logged here too.

    Returns:
        The embedding mapping, or ``None`` when no screenshot could be
        captured or encoded, identical downstream to "no screenshot taken".

    Notes:
        Every ordinary ``Exception`` is suppressed and logged once, here and in
        :func:`capture_png`, while ``KeyboardInterrupt`` and ``SystemExit``
        propagate.  Whether the scenario failed is the caller's test, as in
        the Java hook: scenario state is never read or mutated.
    """
    png = capture_png(driver, scenario_id=scenario_id)
    if png is None:
        return None

    try:
        return build_embedding(png, scenario_name)
    except Exception:
        # Encoding a validated ``bytes`` payload does not fail in practice, but
        # no ordinary exception raised while gathering optional failure
        # evidence may reach the scenario lifecycle and turn a test result into
        # an error.  ``BaseException`` is not caught: an interrupt still ends
        # the run.
        fmt, args = _suppression_record(scenario_id)
        logger.exception(fmt, *args)
        return None
