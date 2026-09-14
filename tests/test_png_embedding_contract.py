"""The inline-PNG embedding contract, at every seam that enforces it.

A report attachment is **result-controlled data that becomes a ``data:`` URI
inside a document a human opens**.  It arrives in the merged result set, which
is assembled from whatever the per-worker event files contain, so its media
type and its payload are both attacker-reachable in any run whose results are
not trusted end to end.  Two consequences follow, and this module exists for
them:

* a media type taken from that data would let a result choose the media type
  of an inline document, and ``image/svg+xml`` is an executable document in a
  browser;
* a payload accepted on the strength of its *declared* media type would let
  base64 of something that is not an image through under the name of one.

``app/reporting/screenshots.py`` is the authority that settles both, and it is
a **structural validator** rather than a signature test: it decodes the
payload and then walks it, CRC-checking every chunk, bounding the geometry the
header declares, requiring a terminal ``IEND`` with nothing behind it, and
requiring the image data to inflate to exactly the size that geometry implies.

The two surfaces that cannot do that -- ``app/templates/partials/lightbox.html``,
which cannot decode base64 at all, and ``app/static/js/report.js``, which has
no ``zlib`` -- apply what they can, and their relationship to the authority is
**one-directional by construction**: they are strictly weaker, so they exist
as defence in depth for a page rendered outside the pipeline and never as the
control that makes an attachment safe.  The tests below pin six properties:

1. the authority's rule set, clause by clause, with a case per clause --
   including the clauses a signature test does not have;
2. that all **three** writers put every attachment through it, so no template
   of either HTML writer and no object in the JSON artifact can be reached by
   one that has not been validated;
3. that the template guard never rejects what the authority accepts, and that
   everything the guard rejects the authority rejects too -- the implication
   that makes it defence in depth, checked over a generated corpus rather than
   over the payloads a review happened to name;
4. that the constants the template and the script compare against are the
   authority's own values, so a bound cannot be changed in one place only;
5. that the producer's size limit cannot exceed what the per-worker result
   schema in ``app/reporting/events.py`` will load back, since a screenshot
   that makes its own shard unloadable is a capture that destroys a whole
   run's results rather than one attachment;
6. that the media type a page receives is a literal of this project's and
   never a value from the input.

The corpus is generated rather than listed, because the interesting cases are
families: every padding class of a **genuinely valid** PNG, every
single-character perturbation of the pad bits in each class, every wrong value
of signature bytes 7 and 8, and one payload per structural defect the format
admits.  A hand-written list covers the case its author thought of.

Every "valid" payload here is built with ``zlib`` and correct CRCs, and the
real-world PNGs on disk are validated alongside them, because a corpus of
plausible-looking fakes would let a validator pass while rejecting every real
screenshot -- the failure mode that costs a project its failure evidence.
"""

from __future__ import annotations

import base64
import re
import struct
import zlib
from collections.abc import Callable
from typing import Any

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.reporting.cucumber_json import build_cucumber_json
from app.reporting.events import MAX_EMBEDDING_DATA_LENGTH
from app.reporting.html_report import (
    decorated_features,
    emitted_features,
    render_html_report,
)
from app.reporting.pretty_reports import render_pretty_pages
from app.reporting.screenshots import (
    DEFAULT_MIME_TYPE,
    MAX_DECOMPRESSED_BYTES,
    MAX_EMBEDDING_BASE64_CHARS,
    MAX_EMBEDDING_BYTES,
    MAX_IMAGE_DIMENSION,
    MAX_IMAGE_PIXELS,
    MAX_PNG_CHUNKS,
    MIN_PNG_BYTES,
    PNG_BASE64_HEADER,
    PNG_BASE64_SIGNATURE,
    PNG_SIGNATURE,
    build_embedding,
    canonical_png_payload,
    capture_png,
    decode_png_payload,
    is_png_bytes,
    normalize_element_attachments,
    normalize_embedding,
    normalize_embeddings,
    normalize_feature_attachments,
    normalize_features_attachments,
    png_defect,
)
from app.utils.paths import static_dir, templates_dir

BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

#: The twelve bytes every valid PNG ends with: a zero-length ``IEND`` chunk,
#: whose CRC is therefore a constant.  Spelled out here independently of the
#: module under test so the corpus's own premise is pinned rather than
#: inherited from the code it checks.
IEND_CHUNK = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def _chunk(kind: bytes, body: bytes) -> bytes:
    """One PNG chunk: length, type, body, and the CRC-32 of type and body."""
    return (
        len(body).to_bytes(4, "big")
        + kind
        + body
        + zlib.crc32(kind + body).to_bytes(4, "big")
    )


def _raw_size(
    width: int, height: int, channels: int, bit_depth: int, interlace: int
) -> int:
    """The decompressed size a PNG of this geometry occupies.

    Written independently of ``app/reporting/screenshots.py``'s own arithmetic
    rather than imported from it: the builder below has to produce image data
    of exactly the right size for a *valid* payload, and if both sides took
    the same formula from one place a mistake in it would cancel out and every
    "valid" case in this module would agree with a wrong validator.

    :param width: Image width in pixels.
    :param height: Image height in pixels.
    :param channels: Samples per pixel.
    :param bit_depth: Bits per sample.
    :param interlace: 0 for none, 1 for Adam7 -- whose seven passes each carry
        their own filter byte per row, so an interlaced image is *larger* raw
        than the same image stored progressively.
    :returns: The byte count, filter bytes included.
    """

    return sum(
        length * count
        for length, count in _row_segments(
            width, height, channels, bit_depth, interlace
        )
    )


def _row_segments(
    width: int, height: int, channels: int, bit_depth: int, interlace: int
) -> list[tuple[int, int]]:
    """How the decompressed data divides into rows: ``(row length, count)``.

    One segment for a progressive image and one per non-empty Adam7 pass, each
    row's length including its leading filter byte.  This is the arithmetic
    :func:`_raw_size` sums and :func:`_raw_rows` fills, kept in one place here
    so a "valid" case and a deliberately mis-filtered one cannot disagree
    about where a row begins -- and written from the specification rather than
    imported from the module under test, so a mistake in the validator's own
    version of it cannot cancel out against this one.

    :param width: Image width in pixels.
    :param height: Image height in pixels.
    :param channels: Samples per pixel.
    :param bit_depth: Bits per sample.
    :param interlace: 0 for none, 1 for Adam7.
    :returns: The segments, in the order the image data stores them.
    """

    def segment(pass_width: int, pass_height: int) -> list[tuple[int, int]]:
        if pass_width <= 0 or pass_height <= 0:
            return []
        return [((pass_width * channels * bit_depth + 7) // 8 + 1, pass_height)]

    if interlace == 0:
        return segment(width, height)
    passes = (
        (0, 0, 8, 8),
        (4, 0, 8, 8),
        (0, 4, 4, 8),
        (2, 0, 4, 4),
        (0, 2, 2, 4),
        (1, 0, 2, 2),
        (0, 1, 1, 2),
    )
    return [
        found
        for x, y, x_step, y_step in passes
        for found in segment(
            -(-(width - x) // x_step) if width > x else 0,
            -(-(height - y) // y_step) if height > y else 0,
        )
    ]


def _raw_rows(
    width: int,
    height: int,
    *,
    channels: int = 4,
    bit_depth: int = 8,
    interlace: int = 0,
    filter_at: Callable[[int], int] = lambda index: 0,
) -> bytes:
    """Decompressed image data of exactly the right size, row by row.

    Every row is a filter byte chosen by *filter_at* followed by zeroed sample
    bytes, so the result is the size the geometry implies whatever filter types
    are used.  That is what separates the filter-framing clause from the size
    clause: a payload built here is the *right length* and differs from a
    valid one only in a byte a decoder must understand.

    :param width: Image width in pixels.
    :param height: Image height in pixels.
    :param channels: Samples per pixel.
    :param bit_depth: Bits per sample.
    :param interlace: 0 for none, 1 for Adam7.
    :param filter_at: Given a row's zero-based index across the whole image --
        counting through the Adam7 passes in storage order -- the filter type
        that row declares.
    :returns: The image data, filter bytes included.
    """
    rows: list[bytes] = []
    for length, count in _row_segments(width, height, channels, bit_depth, interlace):
        for _ in range(count):
            rows.append(bytes((filter_at(len(rows)),)) + bytes(length - 1))
    return b"".join(rows)


def _png(
    width: int = 1,
    height: int = 1,
    *,
    bit_depth: int = 8,
    colour_type: int = 6,
    interlace: int = 0,
    raw: bytes | None = None,
    before: bytes = b"",
    after: bytes = b"",
    idat_parts: int = 1,
    level: int = 9,
    end: bytes = IEND_CHUNK,
) -> bytes:
    """Build a genuinely valid PNG, or a deliberately defective one.

    Every default produces a file a strict decoder reads: correct chunk CRCs,
    a compressed stream that inflates to exactly the size the header implies,
    and a terminal ``IEND``.  The keyword arguments exist so a single clause of
    that can be broken on purpose -- ``end=b""`` removes the terminator,
    ``raw`` supplies image data of the wrong size, ``before`` and ``after``
    insert extra chunks -- which is how the rejected families below are built
    without hand-assembling bytes.

    :param width: Image width in pixels.
    :param height: Image height in pixels.
    :param bit_depth: Bits per sample.
    :param colour_type: PNG colour type; 6 is truecolour with alpha.
    :param interlace: 0 for none, 1 for Adam7.
    :param raw: Decompressed image data.  Defaults to zero bytes of exactly
        the size the geometry implies.
    :param before: Chunks to place between ``IHDR`` and the image data.
    :param after: Chunks to place between the image data and ``IEND``.
    :param idat_parts: How many ``IDAT`` chunks to split the stream across,
        which is what a real encoder does for anything but a tiny image.
    :param level: zlib compression level; 0 stores rather than compresses.
    :param end: The terminating chunk, replaceable for the defective cases.
    :returns: The assembled PNG bytes.
    """
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour_type]
    if raw is None:
        raw = b"\x00" * _raw_size(width, height, channels, bit_depth, interlace)
    header = struct.pack(
        ">IIBBBBB", width, height, bit_depth, colour_type, 0, 0, interlace
    )
    stream = zlib.compress(raw, level)
    size = max(1, len(stream) // idat_parts)
    pieces = [stream[at : at + size] for at in range(0, len(stream), size)] or [b""]
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + before
        + b"".join(_chunk(b"IDAT", piece) for piece in pieces)
        + after
        + end
    )


#: A genuinely valid 1x1 PNG -- one opaque red pixel -- and its canonical
#: base64.  Real rather than plausible: every CRC is correct and the image data
#: inflates to the five bytes its header declares, so it is what the authority
#: accepts and what a browser renders.
VALID_PNG = _png(raw=b"\x00" + bytes((255, 0, 0, 255)))
VALID_PAYLOAD = base64.b64encode(VALID_PNG).decode("ascii")


# --------------------------------------------------------------------------- #
# Corpus construction
# --------------------------------------------------------------------------- #


def _valid_payloads() -> list[str]:
    """Valid payloads covering every padding class, several times over.

    A payload's encoded length mod 4 is fixed by its byte length mod 3, so all
    three padding classes are reached by varying the file length one byte at a
    time -- done here by growing a ``tEXt`` chunk, which leaves every other
    clause of the format satisfied.
    """
    return [
        base64.b64encode(
            _png(before=_chunk(b"tEXt", b"Comment\x00" + b"x" * extra))
        ).decode("ascii")
        for extra in range(9)
    ]


def _real_world_pngs() -> list[bytes]:
    """Every PNG tracked in this repository, read from disk.

    The corpus above is synthetic, and a validator can be wrong in a way only
    a real encoder's output exposes -- a multi-chunk ``IDAT`` run, an
    ancillary chunk, a palette.  These files were produced by real tools, so
    accepting them is the property that keeps the validator from rejecting
    every screenshot the port will ever take.
    """
    return [path.read_bytes() for path in sorted(static_dir().rglob("*.png"))]


#: Canonical base64 of exactly the eight signature bytes and nothing else.
#:
#: **This is the payload the review named**, and it is rejected: a signature
#: with no ``IHDR``, no image data and no ``IEND`` is not a PNG, and admitting
#: it turned eleven characters of base64 into an image ``data:`` URI in a
#: document a human opens.  It is kept as a case of its own because it is the
#: one payload that a signature test and a structural validator disagree
#: about, so it is what tells the two apart.
SIGNATURE_ONLY_PAYLOAD = base64.b64encode(PNG_SIGNATURE).decode("ascii")


def _accepted_payloads() -> list[str]:
    return [
        *_valid_payloads(),
        *(base64.b64encode(png).decode("ascii") for png in _real_world_pngs()),
        # A palette image, an interlaced one, a multi-chunk image and a stored
        # (uncompressed) one: four shapes a real encoder emits and a validator
        # written only against the simple case would reject.
        base64.b64encode(
            _png(
                4,
                4,
                colour_type=3,
                before=_chunk(b"PLTE", bytes(range(12))),
                raw=(b"\x00" + b"\x00\x01\x02\x03") * 4,
            )
        ).decode("ascii"),
        base64.b64encode(_png(17, 13, interlace=1)).decode("ascii"),
        base64.b64encode(_png(64, 64, colour_type=2, idat_parts=7)).decode("ascii"),
        base64.b64encode(_png(32, 32, colour_type=2, level=0)).decode("ascii"),
    ]


def _pad_bit_perturbations() -> list[str]:
    """Payloads differing from a valid one in **nothing but their pad bits**.

    The character before the padding run carries bits the padding makes
    unused, and canonical base64 requires them to be zero.  Only those bits
    are varied here: one padding character leaves the final data character's
    low **two** bits unused and two padding characters leave its low **four**,
    so the perturbation keeps the remaining high bits exactly as they were.

    Varying the whole character instead -- the obvious way to write this --
    would also change *data* bits, and the result would be a different but
    perfectly canonical payload that the guard is right to accept.  That
    distinction is the whole point of the family: every member here decodes
    successfully under ``base64.b64decode(validate=True)`` and every member
    must still be rejected, which no test can show unless the family really
    contains only non-canonical spellings.
    """
    perturbed: list[str] = []
    for payload in _valid_payloads():
        pads = payload.count("=")
        if not pads:
            continue
        pad_bits = 2 if pads == 1 else 4
        index = len(payload) - pads - 1
        value = BASE64_ALPHABET.index(payload[index])
        kept = value & ~((1 << pad_bits) - 1)
        for junk in range(1, 1 << pad_bits):
            replacement = BASE64_ALPHABET[kept | junk]
            perturbed.append(payload[:index] + replacement + payload[index + 1 :])
    return perturbed


def _wrong_signature_payloads() -> list[str]:
    """Payloads agreeing with PNG in six signature bytes and differing in two.

    These are the family a base64-prefix test of eight characters cannot
    reject, because eight characters are fixed by six bytes alone.
    """
    return [
        base64.b64encode(PNG_SIGNATURE[:6] + bytes([high, low]) + b"tail").decode("ascii")
        for high in (0x00, 0x1A, 0xFF)
        for low in (0x00, 0x0A, 0xFF)
        if (high, low) != (0x1A, 0x0A)
    ]


def _malformed_payloads() -> list[str]:
    """Hand-chosen encoding shapes, each naming the clause it violates."""
    return [
        "",  # empty
        "iVBORw0K",  # the old six-byte prefix, and nothing else
        "iVBORw0KGgo",  # length not a multiple of four
        "iVBORw0KGg==",  # truncated before the signature completes
        "=iVBORw0KGgo",  # padding first
        "iVBORw0K=Ggo",  # padding in the interior
        "iVBORw0KGg=o",  # padding followed by data
        "iVBORw0KGgoAAAANSUhEU===",  # three padding characters
        "iVBORw0KGgoA----",  # URL-safe alphabet
        "iVBORw0KGgoA____",  # URL-safe alphabet
        "iVBORw0KGgoA@@@=",  # characters outside any base64 alphabet
        "iVBORw0KGgoA    ",  # trailing whitespace
        "iVBORw0\nKGgoAAAA",  # an embedded newline
        base64.b64encode(b"<svg xmlns='x' onload='alert(1)'/>").decode("ascii"),
        base64.b64encode(b"\xff\xd8\xff\xe0JFIF").decode("ascii"),  # a JPEG
        base64.b64encode(b"GIF89a").decode("ascii"),  # a GIF
        base64.b64encode(b"\x89PNG\r\n\x1a").decode("ascii"),  # signature one byte short
        # A payload whose base64 is impeccable and whose *size* is not: one
        # byte past the decoded ceiling, which is the bound the shard schema
        # shares.
        base64.b64encode(_png() + b"\x00" * MAX_EMBEDDING_BYTES).decode("ascii"),
    ]


#: One payload per structural defect the format admits, each a valid PNG in
#: every respect but one.  This is the family a signature test cannot see at
#: all, and every member is a concrete thing a signature test admitted: a
#: signature with junk behind it, a truncated capture, a corrupted image, a
#: polyglot, and a header whose declared geometry is a decompression bomb.
def _structural_defect_payloads() -> dict[str, bytes]:
    """Defective images, keyed by the defect, so a failure names itself."""
    good = _png(8, 8)
    middle = len(good) // 2
    return {
        "signature-only": PNG_SIGNATURE,
        "signature-and-filler": PNG_SIGNATURE + b"filler" * 16,
        "truncated-mid-chunk": good[:-20],
        "polyglot-behind-iend": good + b"<script>alert(1)</script>",
        "chunk-behind-iend": good + _chunk(b"tEXt", b"k\x00v"),
        "corrupted-ihdr-crc": good[:29] + bytes((good[29] ^ 0xFF,)) + good[30:],
        "corrupted-image-data": (
            good[:middle] + bytes((good[middle] ^ 0xFF,)) + good[middle + 1 :]
        ),
        # Padded with a comment chunk rather than with trailing bytes, so the
        # missing-IDAT clause is what refuses it and not the bytes-after-IEND
        # one: a case that trips two clauses asserts neither.
        "no-idat": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _chunk(b"tEXt", b"Comment\x00padding to clear the length floor")
        + IEND_CHUNK,
        "no-iend": _png(2, 2, end=b"") + _chunk(b"tEXt", b"k\x00v"),
        "ends-inside-a-chunk-header": _png(2, 2, end=b"") + b"\x00\x00\x00\x00\x00",
        "impossible-chunk-length": PNG_SIGNATURE
        + b"\xff\xff\xff\xff"
        + b"IHDR"
        + b"\x00" * 13
        + b"\x00" * 4
        + b"\x00" * MIN_PNG_BYTES,
        "short-ihdr-body": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBB", 1, 1, 8, 6, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 5))
        + IEND_CHUNK,
        "undefined-filter-method": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 1, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 5))
        + IEND_CHUNK,
        "two-palettes": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0))
        + _chunk(b"PLTE", bytes(range(3)))
        + _chunk(b"PLTE", bytes(range(3)))
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + IEND_CHUNK,
        "ragged-palette": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0))
        + _chunk(b"PLTE", bytes(range(4)))
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + IEND_CHUNK,
        "empty-palette": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0))
        + _chunk(b"PLTE", b"")
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + IEND_CHUNK,
        # The compressed stream stops one adler32 short of complete, which no
        # error reports: the inflater simply never reaches its end.
        "truncated-compressed-stream": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00" + bytes(4))[:-4])
        + IEND_CHUNK,
        "data-behind-the-compressed-stream": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00" + bytes(4)) + b"extra")
        + IEND_CHUNK,
        "too-many-chunks": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _chunk(b"tEXt", b"k\x00v") * (MAX_PNG_CHUNKS + 1)
        + _chunk(b"IDAT", zlib.compress(b"\x00" + bytes(4)))
        + IEND_CHUNK,
        "iend-with-a-body": _png(2, 2, end=_chunk(b"IEND", b"x")),
        "zero-width": _png(0, 1, raw=b"\x00"),
        "zero-height": _png(1, 0, raw=b""),
        "geometry-mismatch": _png(4, 4, raw=b"\x00" * 17),
        "unreadable-image-data": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", b"\x00" * 40)
        + IEND_CHUNK,
        "dimension-bomb": PNG_SIGNATURE
        + _chunk(
            b"IHDR",
            struct.pack(
                ">IIBBBBB", MAX_IMAGE_DIMENSION + 1, MAX_IMAGE_DIMENSION + 1, 8, 6, 0, 0, 0
            ),
        )
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 64))
        + IEND_CHUNK,
        "pixel-bomb": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 16000, 16000, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 64))
        + IEND_CHUNK,
        # 16000x2200 sixteen-bit RGBA: inside the dimension bound, inside the
        # forty-million-pixel bound at 35.2 million, and 281 MB decompressed --
        # so only the decompressed-byte budget refuses it.
        "decompressed-byte-bomb": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 16000, 2200, 16, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 64))
        + IEND_CHUNK,
        "impossible-bit-depth": _png(2, 2, bit_depth=2, raw=b"\x00" * 8),
        "undefined-colour-type": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 5, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 5))
        + IEND_CHUNK,
        "undefined-compression-method": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 1, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 5))
        + IEND_CHUNK,
        "undefined-interlace-method": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 2))
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 5))
        + IEND_CHUNK,
        "indexed-without-palette": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + IEND_CHUNK,
        "palette-on-greyscale": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
        + _chunk(b"PLTE", bytes(range(3)))
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + IEND_CHUNK,
        "palette-behind-image-data": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + _chunk(b"PLTE", bytes(range(3)))
        + IEND_CHUNK,
        "palette-too-large-for-depth": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 1, 3, 0, 0, 0))
        + _chunk(b"PLTE", bytes(range(12)))
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + IEND_CHUNK,
        "non-letter-chunk-type": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _chunk(b"1234", b"")
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 5))
        + IEND_CHUNK,
        "chunk-length-overrun": PNG_SIGNATURE
        + (0x7FFFFFF0).to_bytes(4, "big")
        + b"IHDR"
        + b"\x00" * 13
        + b"\x00" * 4
        + b"\x00" * MIN_PNG_BYTES,
        "split-image-data-run": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 5)[:4])
        + _chunk(b"tEXt", b"k\x00v")
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 5)[4:])
        + IEND_CHUNK,
        "second-ihdr": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 5))
        + IEND_CHUNK,
        "ancillary-chunk-first": PNG_SIGNATURE
        + _chunk(b"tEXt", b"k\x00v")
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 5))
        + IEND_CHUNK,
        # ---------------------------------------------------------------- #
        # Scanline framing.  Each of these inflates to *exactly* the size its
        # header declares, so the size clause is satisfied and only the row
        # framing refuses them: the image data is the right length and still
        # describes rows no decoder can reconstruct.
        # ---------------------------------------------------------------- #
        "undefined-filter-type": _png(raw=_raw_rows(1, 1, filter_at=lambda _: 5)),
        "undefined-filter-type-in-a-later-row": _png(
            4, 3, raw=_raw_rows(4, 3, filter_at=lambda index: 0 if index < 2 else 9)
        ),
        "undefined-filter-type-in-a-later-adam7-pass": _png(
            5,
            5,
            interlace=1,
            raw=_raw_rows(
                5,
                5,
                interlace=1,
                filter_at=lambda index: 0 if index < 4 else 200,
            ),
        ),
        # ---------------------------------------------------------------- #
        # Chunk types.  A decoder must understand every critical chunk it
        # meets, so one this format does not define cannot be skipped over;
        # a chunk type's third letter carries the reserved bit, which the
        # specification requires to be upper case; and an ancillary chunk
        # carrying a *second* deflate stream would inflate outside the budget
        # this validator applies to the image data.
        # ---------------------------------------------------------------- #
        "unknown-critical-chunk": _png(before=_chunk(b"ABCD", b"")),
        "reserved-bit-not-zero": _png(before=_chunk(b"aaad", b"")),
        "icc-profile-chunk": _png(
            before=_chunk(b"iCCP", b"p\x00\x00" + zlib.compress(b"\x00" * 128))
        ),
        "compressed-text-chunk": _png(
            before=_chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(b"x" * 128))
        ),
        "international-text-chunk": _png(
            before=_chunk(b"iTXt", b"Comment\x00\x01\x00\x00\x00" + zlib.compress(b"x"))
        ),
        "animation-control-chunk": _png(
            before=_chunk(b"acTL", struct.pack(">II", 2, 0))
        ),
        "animation-frame-data-chunk": _png(
            after=_chunk(b"fdAT", (1).to_bytes(4, "big") + zlib.compress(b"\x00" * 5))
        ),
        "private-ancillary-chunk": _png(before=_chunk(b"prIv", b"\x00")),
        # A suggested palette is legal on truecolour, and sixteen-bit depth
        # could index 65536 entries -- but the format caps PLTE at 256
        # whatever the depth, so this case reaches the ceiling alone.
        "palette-above-the-formats-ceiling": PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 16, 2, 0, 0, 0))
        + _chunk(b"PLTE", bytes(257 * 3))
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 7))
        + IEND_CHUNK,
    }


def _structural_defect_encodings() -> list[str]:
    """The structural family as canonical base64, for the payload-level tests."""
    return [
        base64.b64encode(payload).decode("ascii")
        for payload in _structural_defect_payloads().values()
    ]


def _rejected_payloads() -> list[str]:
    return (
        _pad_bit_perturbations()
        + _wrong_signature_payloads()
        + _malformed_payloads()
        + _structural_defect_encodings()
        + [SIGNATURE_ONLY_PAYLOAD]
    )


def _corpus() -> list[str]:
    return _accepted_payloads() + _rejected_payloads()


# --------------------------------------------------------------------------- #
# The authority's rule set
# --------------------------------------------------------------------------- #


def test_the_encoded_bound_is_the_shard_schemas_own_bound() -> None:
    """The producer cannot capture a screenshot its own shard refuses to load.

    ``app/reporting/events.py`` rejects a per-worker result document whose
    embedding ``data`` exceeds ``MAX_EMBEDDING_DATA_LENGTH`` characters.  When
    the producer's ceiling was higher than that -- it was twice as high, 32 MiB
    decoded against 16 MiB of base64 -- a capture inside the producer's limit
    and outside the schema's made the *whole shard* unloadable, so one large
    screenshot destroyed every result the worker had collected.  The two
    numbers are therefore one number, and this is where that is enforced.
    """
    assert MAX_EMBEDDING_BASE64_CHARS == MAX_EMBEDDING_DATA_LENGTH

    # The decoded ceiling is derived from the encoded one, so the largest
    # payload the producer can emit encodes to exactly the schema's limit --
    # not one character more.
    assert len(base64.b64encode(b"\x00" * MAX_EMBEDDING_BYTES)) == MAX_EMBEDDING_DATA_LENGTH
    assert MAX_EMBEDDING_BYTES == (MAX_EMBEDDING_BASE64_CHARS // 4) * 3

    # And the bound is enforced on the encoded length before any decode, so an
    # oversized payload is rejected rather than expanded.
    assert decode_png_payload("A" * (MAX_EMBEDDING_BASE64_CHARS + 4)) is None
    assert png_defect(b"\x00" * (MAX_EMBEDDING_BYTES + 1)) is not None


def test_png_base64_header_constant_matches_every_real_encoding() -> None:
    """The twenty-character prefix is arithmetic, so it is measured not assumed.

    Every valid PNG opens with the same sixteen bytes -- signature, the
    four-byte length 13, and ``IHDR`` -- so the first twenty characters of its
    canonical base64 are fixed.  Checked against the synthetic corpus *and*
    against the PNGs tracked in this repository, because the claim is about
    real encoders' output and not about this module's builder.
    """
    assert PNG_BASE64_HEADER == "iVBORw0KGgoAAAANSUhE"
    assert len(PNG_BASE64_HEADER) == 20
    assert PNG_BASE64_HEADER.startswith(PNG_BASE64_SIGNATURE)

    real = _real_world_pngs()
    assert real, "the repository tracks at least one PNG for this to mean anything"
    for payload in [*_valid_payloads(), *(base64.b64encode(p).decode() for p in real)]:
        assert payload.startswith(PNG_BASE64_HEADER), payload[:32]

    # The prefix pins sixteen whole bytes: the signature, the length of the
    # first chunk, and the first three characters of its type.
    decoded = base64.b64decode(PNG_BASE64_HEADER)
    assert decoded == PNG_SIGNATURE + (13).to_bytes(4, "big") + b"IHD"


def test_png_base64_signature_constant_matches_a_real_encoding() -> None:
    """The eleven-character prefix is arithmetic, so it is checked not assumed.

    The template guard cannot decode, so it compares characters; the constant
    it compares against is only correct if it really is the prefix a genuine
    PNG encoding produces.  Every padding class is checked, because the prefix
    must not depend on the file's length.
    """
    assert PNG_BASE64_SIGNATURE == "iVBORw0KGgo"
    for payload in _valid_payloads():
        assert payload.startswith(PNG_BASE64_SIGNATURE)
    # And it pins all eight signature bytes: the eleven characters cover bytes
    # 1-8 plus the top two bits of byte 9.
    decoded = base64.b64decode(PNG_BASE64_SIGNATURE + "A")
    assert decoded[:8] == PNG_SIGNATURE


def test_png_signature_is_the_full_eight_bytes() -> None:
    assert PNG_SIGNATURE == b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a"
    assert len(PNG_SIGNATURE) == 8


def test_is_png_bytes_requires_a_structurally_valid_png() -> None:
    """The predicate is the whole rule, not its first eight bytes.

    The four negatives after the signature cases are the point: each one *has*
    PNG's complete signature, so each one passed the test this replaced.
    """
    assert is_png_bytes(VALID_PNG)
    assert not is_png_bytes(PNG_SIGNATURE[:6] + b"\x00\x00tail")
    assert not is_png_bytes(PNG_SIGNATURE[:7])
    assert not is_png_bytes(b"")
    # Signed, and still not a PNG.
    assert not is_png_bytes(PNG_SIGNATURE)
    assert not is_png_bytes(PNG_SIGNATURE + b"filler" * 16)
    assert not is_png_bytes(VALID_PNG[:-12])
    assert not is_png_bytes(VALID_PNG + b"<script>alert(1)</script>")
    # A bytes-like that is not bytes is rejected rather than coerced.
    assert not is_png_bytes(bytearray(VALID_PNG))
    assert not is_png_bytes(VALID_PAYLOAD)
    assert not is_png_bytes(None)


def test_decode_accepts_a_valid_payload_in_every_padding_class() -> None:
    classes = {len(payload) % 4 for payload in _valid_payloads()}
    assert classes == {0}, "every base64 length is a multiple of four"
    assert {payload.count("=") for payload in _valid_payloads()} == {0, 1, 2}
    for payload in _valid_payloads():
        assert decode_png_payload(payload) is not None, payload
        assert decode_png_payload(payload) == base64.b64decode(payload)


def test_decode_rejects_a_file_that_is_only_the_signature() -> None:
    """The payload the review named, rejected at every seam that sees it.

    ``iVBORw0KGgo=`` is canonical base64 of PNG's eight signature bytes and
    nothing else: no ``IHDR``, no image data, no ``IEND``.  A signature test
    calls it a PNG, so it became an ``image/png`` ``data:`` URI in a document
    a human opens -- a payload of the result's choosing, rendered as an image.
    A structural validator calls it what it is, and the length floor alone
    already refuses it.
    """
    assert decode_png_payload(SIGNATURE_ONLY_PAYLOAD) is None
    assert not is_png_bytes(PNG_SIGNATURE)
    assert png_defect(PNG_SIGNATURE) is not None
    assert normalize_embedding(
        {"mime_type": "image/png", "data": SIGNATURE_ONLY_PAYLOAD}
    ) is None
    # And the same for a signature with arbitrary bytes behind it, which is
    # the same defect with the length floor satisfied.
    filler = base64.b64encode(PNG_SIGNATURE + b"filler" * 16).decode("ascii")
    assert decode_png_payload(filler) is None


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ("iVBORw0KAAAA", "signature bytes 7-8 are 00 00, not 1A 0A"),
        ("iVBORw0KAB==", "a non-zero unused pad bit"),
        (base64.b64encode(b"<svg/>").decode("ascii"), "base64 of an SVG document"),
        ("", "empty"),
    ],
)
def test_decode_rejects_the_payloads_a_prefix_test_admits(payload: str, why: str) -> None:
    """The four cases a six-byte prefix plus an alphabet scan lets through."""
    assert decode_png_payload(payload) is None, why


def test_the_validator_accepts_every_real_and_well_formed_png() -> None:
    """The half of the contract a strict validator gets wrong by being strict.

    A validator that rejects real screenshots deletes the evidence a failing
    test run exists to produce, and it does so silently.  Every shape a real
    encoder emits is therefore asserted individually: the PNGs tracked in this
    repository, all five colour types, every bit depth each permits, both
    interlace methods, a multi-chunk image, a stored (uncompressed) one, and
    an image carrying the ancillary chunks encoders routinely add.
    """
    for payload in _real_world_pngs():
        assert png_defect(payload) is None, payload[:32]

    for colour_type, depths in ((0, (1, 2, 4, 8, 16)), (2, (8, 16)), (4, (8, 16)), (6, (8, 16))):
        for bit_depth in depths:
            image = _png(7, 5, bit_depth=bit_depth, colour_type=colour_type)
            assert png_defect(image) is None, (colour_type, bit_depth)

    for bit_depth in (1, 2, 4, 8):
        entries = min(1 << bit_depth, 256)
        image = _png(
            4,
            4,
            bit_depth=bit_depth,
            colour_type=3,
            before=_chunk(b"PLTE", bytes(entries * 3)),
        )
        assert png_defect(image) is None, bit_depth

    for interlace in (0, 1):
        for width, height in ((1, 1), (9, 5), (17, 13), (64, 64)):
            image = _png(width, height, interlace=interlace)
            assert png_defect(image) is None, (interlace, width, height)

    assert png_defect(_png(64, 64, colour_type=2, idat_parts=9)) is None
    assert png_defect(_png(32, 32, colour_type=2, level=0)) is None
    assert (
        png_defect(
            _png(
                4,
                4,
                before=_chunk(b"gAMA", (45455).to_bytes(4, "big"))
                + _chunk(b"pHYs", struct.pack(">IIB", 2835, 2835, 1))
                + _chunk(b"tEXt", b"Software\x00a real encoder"),
                after=_chunk(b"tIME", struct.pack(">HBBBBB", 2026, 9, 14, 12, 0, 0)),
            )
        )
        is None
    )

    # Every filter type the specification defines, on every row, progressively
    # and through Adam7's passes.  An encoder chooses a filter per row on the
    # merits of that row's data, so all five appear in real images and a
    # validator that understood only the common one would reject them.
    for filter_type in (0, 1, 2, 3, 4):
        for interlace in (0, 1):
            image = _png(
                6,
                5,
                interlace=interlace,
                raw=_raw_rows(
                    6, 5, interlace=interlace, filter_at=lambda _, f=filter_type: f
                ),
            )
            assert png_defect(image) is None, (filter_type, interlace)

    # A row's filter type varies row by row, which is the whole point of the
    # per-row byte: an image cycling through all five is what an encoder
    # actually produces.
    mixed = _png(6, 5, raw=_raw_rows(6, 5, filter_at=lambda index: index % 5))
    assert png_defect(mixed) is None

    # The whole set of ancillary chunks an attachment may carry, together, in
    # both of the positions the format allows -- measured from what real
    # captures and this repository's own PNGs contain, so the allowlist cannot
    # reject a legitimate screenshot.
    assert (
        png_defect(
            _png(
                4,
                4,
                colour_type=2,
                before=_chunk(b"cHRM", bytes(32))
                + _chunk(b"gAMA", (45455).to_bytes(4, "big"))
                + _chunk(b"sBIT", bytes((8, 8, 8)))
                + _chunk(b"sRGB", b"\x00")
                + _chunk(b"bKGD", bytes(6))
                + _chunk(b"pHYs", struct.pack(">IIB", 2835, 2835, 1))
                + _chunk(b"sPLT", b"name\x00\x08" + bytes(6))
                + _chunk(b"tRNS", bytes(6))
                + _chunk(b"tEXt", b"Title\x00a real capture"),
                after=_chunk(b"tIME", struct.pack(">HBBBBB", 2026, 9, 14, 12, 0, 0)),
            )
        )
        is None
    )

    # ``hIST`` is the one allowlisted chunk that only means anything beside a
    # palette, so it is asserted on an indexed image rather than in the set
    # above -- the file it appears in has to be one a decoder would accept.
    assert (
        png_defect(
            _png(
                4,
                4,
                colour_type=3,
                before=_chunk(b"PLTE", bytes(3 * 4))
                + _chunk(b"hIST", bytes(2 * 4))
                + _chunk(b"tRNS", bytes(4)),
            )
        )
        is None
    )


@pytest.mark.parametrize("defect", sorted(_structural_defect_payloads()))
def test_the_validator_rejects_each_structural_defect(defect: str) -> None:
    """One case per structural defect, each reported with a reason.

    Every payload here carries PNG's complete eight-byte signature, so every
    one of them passed the signature test this replaced -- which is what makes
    the list a record of what that test admitted rather than a list of obvious
    junk.  The reason is asserted to be non-empty and to name no payload
    bytes, because it is written to a log an operator reads.
    """
    payload = _structural_defect_payloads()[defect]
    reason = png_defect(payload)

    assert reason is not None, defect
    assert reason.strip()
    assert "\n" not in reason
    if defect not in {"non-letter-chunk-type", "chunk-length-overrun"}:
        assert payload.startswith(PNG_SIGNATURE), defect
    assert not is_png_bytes(payload)
    assert decode_png_payload(base64.b64encode(payload).decode("ascii")) is None


def test_the_validator_bounds_the_work_a_payload_can_demand() -> None:
    """The decompression-bomb controls, stated as the numbers they enforce.

    The geometry is refused before a byte is inflated, so a payload declaring
    a vast image costs a header read rather than an allocation -- and the
    running total during inflation is what catches a stream whose actual
    output exceeds its own declared size.
    """
    assert MAX_IMAGE_DIMENSION == 16384
    assert MAX_IMAGE_PIXELS == 40_000_000
    assert MAX_DECOMPRESSED_BYTES == 256 * 1024 * 1024
    assert MAX_PNG_CHUNKS == 65536

    # A 16000x16000 header is 256 million pixels: inside the dimension bound
    # and far outside the pixel bound, which is why both exist.
    pixel_bomb = _structural_defect_payloads()["pixel-bomb"]
    assert "pixel" in png_defect(pixel_bomb)

    # 16000x2200 sixteen-bit RGBA is inside both the dimension and the pixel
    # bounds -- 35.2 million pixels -- and implies 281 MB decompressed, which
    # only the byte bound sees.
    byte_bomb = _structural_defect_payloads()["decompressed-byte-bomb"]
    assert "decompressed" in png_defect(byte_bomb)

    # A stream that inflates to more than its header declares is caught while
    # inflating, by the running total rather than by the geometry.
    lying = _png(4, 4, raw=b"\x00" * (_raw_size(4, 4, 4, 8, 0) + 4096))
    assert png_defect(lying) is not None

    # The chunk cap is enforced, and comfortably above what a real encoder
    # produces: a payload at the decoded ceiling arrives from Chrome in
    # roughly three thousand four-kilobyte IDAT chunks.
    assert MAX_PNG_CHUNKS > MAX_EMBEDDING_BYTES // (4 * 1024)


def test_the_image_data_is_framed_into_rows_and_not_merely_counted() -> None:
    """Inflating to the right size is not the same as being an image.

    The size clause asks how many bytes the image data comes to; this clause
    asks what those bytes *say*.  A row begins with a filter type, of which
    the specification defines five, and a payload whose rows declare anything
    else is one no decoder can reconstruct -- so counting bytes alone accepts
    a file that is the right length and still not an image.

    The cases are built to isolate that: each differs from an accepted payload
    in **one byte**, the total is identical, and the pairs are asserted
    together so the reason cannot be the size.  The third case puts the bad
    byte in a later Adam7 pass, where a validator that checked only the first
    row -- or only the first pass -- would miss it.
    """
    valid = _raw_rows(4, 3)
    broken = _raw_rows(4, 3, filter_at=lambda index: 0 if index < 2 else 9)

    assert len(valid) == len(broken) == _raw_size(4, 3, 4, 8, 0)
    assert sum(left != right for left, right in zip(valid, broken, strict=True)) == 1
    assert png_defect(_png(4, 3, raw=valid)) is None
    assert "filter type 9" in png_defect(_png(4, 3, raw=broken))

    for defect, filter_type in (
        ("undefined-filter-type", 5),
        ("undefined-filter-type-in-a-later-row", 9),
        ("undefined-filter-type-in-a-later-adam7-pass", 200),
    ):
        reason = png_defect(_structural_defect_payloads()[defect])
        assert f"filter type {filter_type}" in reason, defect
        assert "does not define" in reason, defect

    # Data that stops short of filling the rows its header declares is caught
    # by the framing too, and reported as such rather than as a filter type:
    # the walker knows it never reached the end of the last row.
    short = _png(4, 3, raw=_raw_rows(4, 3)[: _raw_size(4, 3, 4, 8, 0) - 1])
    assert png_defect(short) is not None


def test_chunk_types_are_held_to_what_a_decoder_can_skip_and_this_can_account_for()\
        -> None:
    """Three rules about a chunk's four letters, and why each one exists.

    A chunk type is not an opaque label: its letter cases carry the rules a
    decoder follows.  This validator applies the two the specification states
    and one of its own, and the third is the reason the other two are not
    enough:

    * **A critical chunk must be one this format defines.**  Critical means a
      decoder may not skip it, so an unknown one cannot be walked past -- a
      payload carrying ``ABCD`` as critical data is asking for a decoder this
      project does not have.
    * **The reserved bit must be clear**, which is to say the third letter is
      upper case.  A payload with it set is written against a version of the
      format that does not exist.
    * **An ancillary chunk must be one an attachment has any business
      carrying.**  This is the validator's own rule and the narrower one: the
      allowlist holds the uncompressed chunks real captures were measured to
      contain, and excludes every chunk carrying a *second* deflate stream --
      ``iCCP``, ``zTXt``, a compressed ``iTXt``, and APNG's ``fdAT`` -- because
      this module's inflation budget covers the image data and nothing else.
      Accepting them would mean a bounded ``IDAT`` beside an unbounded
      profile, which is the decompression bomb the budget exists to stop
      wearing a different chunk type.

    Every rejection names the chunk, since the reason reaches a log an
    operator reads and "a chunk is wrong" would not be actionable.
    """
    payloads = _structural_defect_payloads()

    assert "ABCD" in png_defect(payloads["unknown-critical-chunk"])
    assert "critical" in png_defect(payloads["unknown-critical-chunk"])
    assert "aaad" in png_defect(payloads["reserved-bit-not-zero"])
    assert "reserved bit" in png_defect(payloads["reserved-bit-not-zero"])

    for defect, chunk in (
        ("icc-profile-chunk", "iCCP"),
        ("compressed-text-chunk", "zTXt"),
        ("international-text-chunk", "iTXt"),
        ("animation-control-chunk", "acTL"),
        ("animation-frame-data-chunk", "fdAT"),
        ("private-ancillary-chunk", "prIv"),
    ):
        reason = png_defect(payloads[defect])
        assert chunk in reason, defect
        assert "may carry" in reason, defect

    # The rule is a rule about the type, not about the body: an allowlisted
    # chunk with the same body is accepted, so nothing here depends on what a
    # chunk contains.
    assert png_defect(_png(before=_chunk(b"tEXt", b"p\x00\x00" + bytes(32)))) is None

    # And the ceiling on a palette is the format's own, independent of what
    # the bit depth could index: sixteen-bit depth could name 65536 entries
    # and PLTE still stops at 256.
    reason = png_defect(payloads["palette-above-the-formats-ceiling"])
    assert "257" in reason
    assert "256" in reason
    assert png_defect(
        PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 16, 2, 0, 0, 0))
        + _chunk(b"PLTE", bytes(256 * 3))
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 7))
        + IEND_CHUNK
    ) is None


def test_strict_decoding_alone_would_accept_every_pad_bit_case() -> None:
    """Why the re-encode comparison exists, demonstrated rather than asserted.

    If ``b64decode(validate=True)`` rejected these, canonicality would be free
    and the comparison would be dead code.  It rejects **none** of them, so the
    comparison is the only thing standing between a report and two spellings of
    one payload.
    """
    perturbed = _pad_bit_perturbations()
    assert len(perturbed) >= 18, "both padding classes must be represented"
    for payload in perturbed:
        # Strict decoding accepts it...
        base64.b64decode(payload, validate=True)
        # ...and it really is a non-canonical spelling of those bytes...
        assert base64.b64encode(base64.b64decode(payload)).decode("ascii") != payload
        # ...whose canonical spelling is a payload the guard accepts, which is
        # what makes this an ambiguity rather than simply a broken input.
        canonical = base64.b64encode(base64.b64decode(payload)).decode("ascii")
        assert decode_png_payload(canonical) is not None
        # ...so only the comparison rejects it.
        assert decode_png_payload(payload) is None, payload


def test_decode_rejects_every_wrong_signature_and_malformed_payload() -> None:
    for payload in _wrong_signature_payloads():
        assert decode_png_payload(payload) is None, payload
    for payload in _malformed_payloads():
        assert decode_png_payload(payload) is None, payload


def test_decode_rejects_non_string_payloads() -> None:
    for value in (None, 42, 3.5, True, b"bytes", ["list"], {"a": 1}, VALID_PNG):
        assert decode_png_payload(value) is None


def test_decode_rejects_an_oversized_payload_without_decoding_it() -> None:
    """The bound is on the encoded length, so the rejection costs no memory."""
    oversized = "A" * (MAX_EMBEDDING_BASE64_CHARS + 4)
    assert decode_png_payload(oversized) is None
    # The bound is the encoded length of the decoded bound, to the character.
    assert MAX_EMBEDDING_BASE64_CHARS == ((MAX_EMBEDDING_BYTES + 2) // 3) * 4


def test_canonical_payload_round_trips_and_rejects_what_decode_rejects() -> None:
    assert canonical_png_payload(VALID_PAYLOAD) == VALID_PAYLOAD
    assert base64.b64decode(canonical_png_payload(VALID_PAYLOAD)) == VALID_PNG
    assert canonical_png_payload("iVBORw0KAB==") is None
    assert canonical_png_payload(None) is None


# --------------------------------------------------------------------------- #
# The normalizer, and the media type it writes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "media_type",
    [
        "image/svg+xml",
        "image/jpeg",
        "image/gif",
        "image/png;charset=utf-8",
        "image/pngx",
        "text/html",
        "image/",
        "image/*",
        "",
        None,
        7,
    ],
)
def test_normalize_rejects_every_media_type_but_png(media_type: Any) -> None:
    """Not a prefix match, not a parameterised variant -- exactly ``image/png``."""
    assert normalize_embedding({"mime_type": media_type, "data": VALID_PAYLOAD}) is None


@pytest.mark.parametrize("media_type", ["image/png", "IMAGE/PNG", " Image/Png "])
def test_normalize_accepts_png_case_insensitively(media_type: str) -> None:
    """A media type is case-insensitive; its surrounding whitespace is noise."""
    normalized = normalize_embedding({"mime_type": media_type, "data": VALID_PAYLOAD})
    assert normalized is not None
    assert normalized["mime_type"] == DEFAULT_MIME_TYPE


def test_normalize_writes_the_media_type_from_a_literal() -> None:
    """The emitted media type is this project's constant, never the input's.

    The accepted spellings above differ from each other; every one of them
    comes out as the same canonical value, which is what makes the media type
    in a rendered ``data:`` URI impossible for a result to choose.
    """
    emitted = {
        normalize_embedding({"mime_type": spelling, "data": VALID_PAYLOAD})["mime_type"]
        for spelling in ("image/png", "IMAGE/PNG", " Image/Png ")
    }
    assert emitted == {DEFAULT_MIME_TYPE}


def test_normalize_rejects_a_png_media_type_with_a_foreign_payload() -> None:
    """The declared media type never vouches for the payload."""
    assert (
        normalize_embedding(
            {
                "mime_type": "image/png",
                "data": base64.b64encode(b"<svg onload='alert(1)'/>").decode("ascii"),
            }
        )
        is None
    )


def test_normalize_carries_a_string_name_and_drops_anything_else() -> None:
    named = normalize_embedding(
        {"mime_type": "image/png", "data": VALID_PAYLOAD, "name": "A 'quoted' name"}
    )
    assert named is not None and named["name"] == "A 'quoted' name"

    for name in (None, 7, ["x"], {"a": 1}):
        normalized = normalize_embedding(
            {"mime_type": "image/png", "data": VALID_PAYLOAD, "name": name}
        )
        assert normalized is not None
        assert "name" not in normalized, name


def test_normalize_rejects_non_mappings() -> None:
    for value in (None, "string", 42, ["list"], VALID_PAYLOAD):
        assert normalize_embedding(value) is None


def test_normalize_embeddings_keeps_order_and_drops_the_rest() -> None:
    second = base64.b64encode(_png(2, 2)).decode("ascii")
    kept = normalize_embeddings(
        [
            {"mime_type": "image/svg+xml", "data": VALID_PAYLOAD, "name": "svg"},
            {"mime_type": "image/png", "data": VALID_PAYLOAD, "name": "first"},
            {"mime_type": "image/png", "data": "iVBORw0KAAAA", "name": "bad-signature"},
            {"mime_type": "image/png", "data": second, "name": "second"},
        ]
    )
    assert [item["name"] for item in kept] == ["first", "second"]


@pytest.mark.parametrize("value", [None, "string", 42, {"mime_type": "image/png"}])
def test_normalize_embeddings_treats_a_non_list_as_no_attachments(value: Any) -> None:
    """A string yields characters and a mapping yields keys; neither is an attachment."""
    assert normalize_embeddings(value) == []


# --------------------------------------------------------------------------- #
# Capture and construction
# --------------------------------------------------------------------------- #


class _Driver:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def get_screenshot_as_png(self) -> Any:
        return self._payload


def test_capture_png_requires_the_driver_to_return_a_png() -> None:
    """The capture gate applies the whole contract, not a signature test.

    A driver answering a truncated or corrupted image is the realistic case --
    a session dying mid-transfer produces exactly that -- and embedding it
    would put a broken image in the report under the media type
    ``image/png``, which is worse than reporting no evidence at all.
    """
    assert capture_png(_Driver(VALID_PNG)) == VALID_PNG
    assert capture_png(_Driver(b"\xff\xd8\xff\xe0JFIF")) is None
    assert capture_png(_Driver(b"<html>an error page</html>")) is None
    assert capture_png(_Driver(PNG_SIGNATURE[:7])) is None
    assert capture_png(_Driver(b"")) is None
    assert capture_png(_Driver(None)) is None
    assert capture_png(_Driver(PNG_SIGNATURE)) is None
    assert capture_png(_Driver(VALID_PNG[:-12])) is None
    assert capture_png(_Driver(VALID_PNG + b"trailing")) is None
    assert capture_png(_Driver(b"\x00" * (MAX_EMBEDDING_BYTES + 1))) is None
    # A bytearray or memoryview of a valid image is still accepted: the
    # normalisation of a well-behaved but unusual return is deliberate, and
    # only the payload's *content* is judged.
    assert capture_png(_Driver(bytearray(VALID_PNG))) == VALID_PNG
    assert capture_png(_Driver(memoryview(VALID_PNG))) == VALID_PNG


def test_capture_png_never_raises_on_a_dead_session() -> None:
    class _Dead:
        def get_screenshot_as_png(self) -> bytes:
            raise RuntimeError("session deleted")

    assert capture_png(_Dead()) is None
    assert capture_png(object()) is None


def test_build_embedding_rejects_a_non_png_and_a_foreign_media_type() -> None:
    assert build_embedding(VALID_PNG, "s")["mime_type"] == DEFAULT_MIME_TYPE
    assert build_embedding(VALID_PNG, "s")["data"] == VALID_PAYLOAD
    with pytest.raises(ValueError, match="must be a valid PNG"):
        build_embedding(b"\xff\xd8jpeg", "s")
    with pytest.raises(ValueError, match="must be a valid PNG"):
        build_embedding(PNG_SIGNATURE, "s")
    with pytest.raises(ValueError, match="must be a valid PNG"):
        build_embedding(VALID_PNG + b"trailing", "s")
    with pytest.raises(ValueError, match="media type"):
        build_embedding(VALID_PNG, "s", mime_type="image/svg+xml")


def test_build_embedding_omits_an_absent_name() -> None:
    assert "name" not in build_embedding(VALID_PNG, None)


# --------------------------------------------------------------------------- #
# The two writers
# --------------------------------------------------------------------------- #


#: A payload every check short of the full structural walk accepts: PNG's
#: complete signature, a real ``IHDR``, correct chunk CRCs, a compressed
#: stream that inflates to exactly the declared size, and a terminal
#: ``IEND`` -- whose single scanline then declares filter type 5, which the
#: format does not define.  It is the payload that showed a signature test,
#: a header test and a size test are each insufficient on their own, so it is
#: carried through the writer and viewer cases below: if the authority ever
#: weakens again, this is the attachment that reaches a report page.
MALFORMED_PNG_PAYLOAD = base64.b64encode(
    _png(raw=_raw_rows(1, 1, filter_at=lambda _: 5))
).decode("ascii")


def _hostile_document() -> dict[str, Any]:
    """A one-feature result set whose failed scenario carries six attachments.

    Five are hostile in a different way and one is a genuine PNG, so a writer
    that dropped validation would show five of them and a writer that dropped
    the attachment support entirely would show none -- the two failure modes
    are distinguishable by the assertions below.  The fifth is the interesting
    one: it is *structurally* malformed rather than obviously wrong, so it is
    the case a media-type test, a signature test and a size test all pass.
    """
    return {
        "features": [
            {
                "uri": "file:features/Crm.feature",
                "id": "crm",
                "keyword": "Feature",
                "line": 1,
                "name": "Attachment contract",
                "description": "",
                "elements": [
                    {
                        "keyword": "Scenario",
                        "type": "scenario",
                        "id": "crm;s",
                        "line": 3,
                        "name": "A failing scenario",
                        "description": "",
                        "start_timestamp": "2022-09-07T13:37:26.297Z",
                        "steps": [
                            {
                                "keyword": "Given ",
                                "name": "a step that fails",
                                "line": 4,
                                "match": {"location": "features/steps/crm_steps.py:1"},
                                "result": {
                                    "status": "failed",
                                    "duration": 1_000_000,
                                    "error_message": "AssertionError: boom",
                                },
                            }
                        ],
                        "after": [
                            {
                                "match": {"location": "features/environment.py:1"},
                                "result": {"status": "passed", "duration": 1_000},
                                "embeddings": [
                                    {
                                        "mime_type": "image/svg+xml",
                                        "data": base64.b64encode(
                                            b"<svg onload='alert(1)'/>"
                                        ).decode("ascii"),
                                        "name": "svg-attachment",
                                    },
                                    {
                                        "mime_type": "image/png",
                                        "data": "iVBORw0KAAAA",
                                        "name": "wrong-signature",
                                    },
                                    {
                                        "mime_type": "image/png",
                                        "data": "iVBORw0KAB==",
                                        "name": "noncanonical-pad-bits",
                                    },
                                    {
                                        "mime_type": "image/jpeg",
                                        "data": VALID_PAYLOAD,
                                        "name": "jpeg-media-type",
                                    },
                                    {
                                        "mime_type": "image/png",
                                        "data": MALFORMED_PNG_PAYLOAD,
                                        "name": "structurally-malformed",
                                    },
                                    {
                                        "mime_type": "image/png",
                                        "data": VALID_PAYLOAD,
                                        "name": "the one good png",
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }


#: Probes that must not appear anywhere in a generated page.  Each is the
#: attachment's ``data:`` URI form rather than its bare payload, and the SVG
#: probe names the base64 encoding specifically, for two reasons that are easy
#: to get wrong:
#:
#: * the artifact legitimately carries an inline **SVG favicon** in a
#:   ``<link rel="icon">``, URL-encoded rather than base64, so a bare
#:   ``data:image/svg`` probe matches the writer's own markup and proves
#:   nothing;
#: * ``report.js`` is inlined verbatim into the artifact and its guard is
#:   **documented** with the very payloads this module rejects, so a bare
#:   ``iVBORw0KAB==`` probe matches a source comment rather than a rendered
#:   attachment.
#:
#: Testing the URI form distinguishes a rendered attachment from prose about
#: one, which is the only thing either probe is for.
#: The structurally malformed payload is probed **whole**, unlike the others:
#: its encoding opens with the same twenty characters every valid PNG does, so
#: a prefix probe would match the good attachment beside it and pass whatever
#: the writers did.
FORBIDDEN_URIS_IN_PAGES = (
    "data:image/svg+xml;base64",
    "data:image/jpeg",
    "data:image/png;base64,iVBORw0KAAAA",
    "data:image/png;base64,iVBORw0KAB==",
    f"data:image/png;base64,{MALFORMED_PNG_PAYLOAD}",
)

#: Probes that identify a *rejected* attachment by its name.  None of these
#: strings occurs in either shared asset, so they may be tested bare.
FORBIDDEN_NAMES_IN_PAGES = (
    "onload=",
    "svg-attachment",
    "wrong-signature",
    "noncanonical-pad-bits",
    "jpeg-media-type",
    "structurally-malformed",
)

FORBIDDEN_IN_PAGES = FORBIDDEN_URIS_IN_PAGES + FORBIDDEN_NAMES_IN_PAGES


def test_html_writer_validates_attachments_before_the_template_sees_them() -> None:
    document = _hostile_document()
    decorated = decorated_features(emitted_features(document))
    surviving = decorated[0]["elements"][0]["after"][0]["embeddings"]
    assert [item["name"] for item in surviving] == ["the one good png"]
    assert {item["mime_type"] for item in surviving} == {DEFAULT_MIME_TYPE}


def test_json_writer_validates_attachments_before_they_reach_the_artifact() -> None:
    """The JSON artifact is validated by the same function as the HTML ones.

    It is the artifact the continuous-integration publisher ingests and the
    viewer renders from, and it used to be the one writer that copied a
    result's declared media type and payload straight through.  That made the
    JSON the single place an ``image/svg+xml`` attachment -- an active document
    in a browser -- or base64 of something that is not an image could survive,
    while the two HTML writers dropped it: four artifacts describing one run
    and disagreeing about its evidence.
    """
    document = build_cucumber_json(_hostile_document())
    embeddings = document[0]["elements"][0]["after"][0]["embeddings"]

    assert [item["name"] for item in embeddings] == ["the one good png"]
    assert {item["mime_type"] for item in embeddings} == {DEFAULT_MIME_TYPE}
    assert embeddings[0]["data"] == VALID_PAYLOAD
    assert set(embeddings[0]) == {"mime_type", "data", "name"}

    # Nothing from a rejected attachment reaches the document at all -- not its
    # name, not its media type and not its payload.
    serialised = repr(document)
    for probe in ("svg+xml", "onload=", "wrong-signature", "noncanonical-pad-bits",
                  "jpeg-media-type", "image/jpeg"):
        assert probe not in serialised, probe


def test_every_writer_reaches_the_same_verdict_on_one_attachment_set() -> None:
    """One merged result set, three artifacts, one verdict per attachment.

    Asserted across the writers rather than inside each, because the defect
    this pins was precisely a *disagreement*: each writer was individually
    self-consistent.
    """
    document = _hostile_document()

    json_names = [
        item["name"]
        for item in build_cucumber_json(document)[0]["elements"][0]["after"][0][
            "embeddings"
        ]
    ]
    html_names = [
        item["name"]
        for item in decorated_features(emitted_features(document))[0]["elements"][0][
            "after"
        ][0]["embeddings"]
    ]
    page = render_html_report(document)
    tree = "".join(render_pretty_pages(document).values())

    assert json_names == html_names == ["the one good png"]
    for surface in (page, tree):
        assert f"data:{DEFAULT_MIME_TYPE};base64,{VALID_PAYLOAD}" in surface
        for probe in FORBIDDEN_IN_PAGES:
            assert probe not in surface, probe


def test_html_writer_does_not_mutate_the_shared_result_set() -> None:
    """Four writers are fed from one merged set; a writer that edited it would
    change what the other three see."""
    document = _hostile_document()
    before = document["features"][0]["elements"][0]["after"][0]["embeddings"]
    decorated_features(emitted_features(document))
    after = document["features"][0]["elements"][0]["after"][0]["embeddings"]
    assert after is before
    assert len(after) == 6


def _image_sources(page: str) -> list[str]:
    """Every ``src`` an ``img`` element in ``page`` carries."""
    return [
        match.group(1)
        for match in re.finditer(r"""<img\b[^>]*?\bsrc=["\']([^"\']*)["\']""", page)
    ]


def test_rendered_artifact_carries_only_the_valid_png() -> None:
    page = render_html_report(_hostile_document())
    for probe in FORBIDDEN_IN_PAGES:
        assert probe not in page, probe
    assert f"data:{DEFAULT_MIME_TYPE};base64,{VALID_PAYLOAD}" in page
    assert "the one good png" in page

    # Every image the page loads is an inline PNG, checked element by element
    # rather than by substring.  The artifact also carries an inline SVG
    # favicon in a <link rel="icon">, which is the writer's own markup and not
    # an attachment -- so a bare "data:image/svg" substring probe would match
    # it and prove nothing, and this assertion is what distinguishes the two.
    sources = _image_sources(page)
    assert sources, "the hostile document has a screenshot, so there is an img"
    for source in sources:
        assert source.startswith(f"data:{DEFAULT_MIME_TYPE};base64,"), source
        assert decode_png_payload(source.split(",", 1)[1]) is not None, source


def test_rendered_pretty_tree_carries_only_the_valid_png() -> None:
    pages = render_pretty_pages(_hostile_document())
    assert pages, "the tree must have pages for this assertion to mean anything"
    joined = "".join(pages.values())
    for probe in FORBIDDEN_IN_PAGES:
        assert probe not in joined, probe
    assert f"data:{DEFAULT_MIME_TYPE};base64,{VALID_PAYLOAD}" in joined
    for source in _image_sources(joined):
        if not source.startswith("data:"):
            # The tree links its own vendored favicon by relative path; only
            # inline sources are the attachment path.
            continue
        assert source.startswith(f"data:{DEFAULT_MIME_TYPE};base64,"), source
        assert decode_png_payload(source.split(",", 1)[1]) is not None, source


def test_flat_embedding_lists_are_validated_too() -> None:
    """``artifact/element.html`` and both viewer templates accept a flat list."""
    element = {
        "type": "scenario",
        "name": "s",
        "embeddings": [
            {"mime_type": "image/svg+xml", "data": VALID_PAYLOAD, "name": "svg"},
            {"mime_type": "image/png", "data": VALID_PAYLOAD, "name": "ok"},
        ],
    }
    normalized = normalize_element_attachments(element)
    assert [item["name"] for item in normalized["embeddings"]] == ["ok"]


def test_a_large_image_is_inflated_a_block_at_a_time() -> None:
    """The bounded-inflation path, exercised with an image bigger than a block.

    The validator inflates :data:`~app.reporting.screenshots.MAX_DECOMPRESSED_BYTES`
    worth of image at most one mebibyte at a time, counting and discarding each
    block, which is what lets the budget be generous without ever allocating
    it.  A 600x600 truecolour-with-alpha image decompresses to about 1.4 MB, so
    it takes more than one block and therefore exercises the loop and its
    drain rather than the single-block shortcut every other case here uses.
    """
    large = _png(600, 600)
    assert png_defect(large) is None
    assert _raw_size(600, 600, 4, 8, 0) > 1024 * 1024
    # Still small enough to be a real attachment: the point of the block
    # bound is that a big *decompressed* image is not a big payload.
    assert len(large) < 64 * 1024


def test_the_palette_check_is_total_even_where_the_walk_cannot_reach_it() -> None:
    """The one guard the chunk walk makes unreachable, pinned as total anyway.

    ``_palette_defect`` takes the validated header, and the walk cannot call it
    without one: a payload whose first chunk is not ``IHDR`` is refused before
    any ``PLTE`` handling runs.  The guard exists so the helper is a total
    function of its arguments rather than one that depends on its caller's
    ordering, and it is exercised directly because that is the only way to
    reach it -- an unreachable branch nobody has ever run is the kind that
    raises ``AttributeError`` the day the walk is reordered.
    """
    from app.reporting.screenshots import _palette_defect

    assert _palette_defect(3, None, saw_palette=False, saw_image_data=False) is not None
    assert "IHDR" in _palette_defect(3, None, saw_palette=False, saw_image_data=False)


def test_the_row_framing_helpers_are_total_functions_of_their_arguments() -> None:
    """The framing guards the walk makes unreachable, pinned as total anyway.

    Three of them, each unreachable from a payload because an earlier clause
    refuses that payload first, and each exercised here for the same reason
    the palette guard is: a branch nobody has ever run is the one that raises
    the day the walk is reordered.

    * **A degenerate geometry produces no layout.**  ``_header_defect``
      refuses a zero dimension before a layout is built, so the guard is what
      keeps the layout function from inventing a row for an image with none.
    * **A stream longer than its layout stops the walker rather than
      overrunning it.**  The size comparison reports that payload, and the
      walker must not index past its own layout while reaching it.
    * **An incomplete walk is reported.**  Held directly against a layout
      whose rows the data does not fill, which is the state the size clause
      normally catches first.

    The walker's incremental behaviour is asserted here too, because it is
    what makes the framing check affordable: a row split across blocks is the
    normal case for a real screenshot, whose rows are thousands of bytes and
    whose inflation proceeds a megabyte at a time.
    """
    from app.reporting.screenshots import (
        _decompressed_bytes,
        _image_data_defect,
        _scanline_layout,
        _ScanlineWalker,
    )

    assert _scanline_layout(0, 4, 4, 8, 0) == ()
    assert _scanline_layout(4, 0, 4, 8, 0) == ()
    assert _decompressed_bytes(()) == 0
    assert _ScanlineWalker(()).complete is True

    # A layout of one two-byte row, fed four bytes: the extra pair is past
    # everything the layout accounts for, and the walker says nothing about
    # it rather than reading a row length that does not exist.
    overrun = _ScanlineWalker(((2, 1),))
    assert overrun.feed(b"\x00\x00\x00\x00") is None
    assert overrun.complete is True

    # Two rows of three bytes, delivered one byte at a time: the filter byte
    # of each row is recognised wherever a block boundary happens to fall.
    split = _ScanlineWalker(((3, 2),))
    assert [split.feed(bytes((byte,))) for byte in b"\x00ab\x04cd"] == [None] * 6
    assert split.complete is True

    # And the same stream with the second row's filter byte undefined is
    # caught on the byte that carries it, not at the end of the image.
    late = _ScanlineWalker(((3, 2),))
    reasons = [late.feed(bytes((byte,))) for byte in b"\x00ab\x07cd"]
    assert reasons[3] is not None
    assert "filter type 7" in reasons[3]

    # An incomplete walk, reported by the walker rather than by the size.
    body = zlib.compress(b"\x00\x00")
    assert _image_data_defect([body], 2, ((2, 1), (2, 1))) is not None
    assert "does not fill the rows" in _image_data_defect([body], 2, ((2, 1), (2, 1)))


def test_feature_level_normalizers_validate_every_element() -> None:
    """The feature and feature-list normalizers, which the writers call first.

    Both HTML writers normalize at the point they assemble the features they
    are about to render, so these are the entry points that put every
    attachment in a run through the contract.  Order is asserted as well as
    content: features stay in source order and elements stay where the model
    put them, which the report contract fixes.
    """
    def element(name: str, payload: str) -> dict[str, Any]:
        return {
            "type": "scenario",
            "name": name,
            "embeddings": [{"mime_type": "image/png", "data": payload, "name": name}],
        }

    feature = {
        "uri": "file:features/Crm.feature",
        "name": "Crm",
        "elements": [element("good", VALID_PAYLOAD), element("bad", "iVBORw0KAAAA")],
    }

    normalized = normalize_feature_attachments(feature)
    assert [item["name"] for item in normalized["elements"]] == ["good", "bad"]
    assert normalized["elements"][0]["embeddings"][0]["data"] == VALID_PAYLOAD
    assert normalized["elements"][1]["embeddings"] == []

    features = normalize_features_attachments([feature, {"uri": "file:features/X.feature"}])
    assert [item["uri"] for item in features] == [
        "file:features/Crm.feature",
        "file:features/X.feature",
    ]
    assert features[0]["elements"][1]["embeddings"] == []
    # The input is untouched: one merged result set feeds every writer.
    assert feature["elements"][1]["embeddings"][0]["data"] == "iVBORw0KAAAA"


def test_normalizers_do_not_invent_keys_an_element_never_had() -> None:
    """An element with no attachments must not come out looking as if it had some."""
    element = {"type": "scenario", "name": "s", "steps": []}
    assert normalize_element_attachments(element) == element
    assert "after" not in normalize_element_attachments(element)
    assert "embeddings" not in normalize_element_attachments(element)

    feature = {"uri": "file:features/X.feature", "name": "X"}
    assert normalize_feature_attachments(feature) == feature
    assert "elements" not in normalize_feature_attachments(feature)


@pytest.mark.parametrize("value", [None, "string", 42, {"uri": "x"}])
def test_feature_normalizers_are_total(value: Any) -> None:
    """Every degenerate input answers a value; nothing raises."""
    assert normalize_features_attachments(value) == []
    assert isinstance(normalize_feature_attachments(value), dict)
    assert isinstance(normalize_element_attachments(value), dict)


def test_malformed_hook_and_element_containers_degrade_to_empty() -> None:
    element = normalize_element_attachments(
        {"type": "scenario", "after": "not-a-list", "embeddings": {"a": 1}}
    )
    assert element["after"] == []
    assert element["embeddings"] == []

    feature = normalize_feature_attachments(
        {"uri": "file:features/X.feature", "elements": "not-a-list"}
    )
    assert feature["elements"] == []


def test_a_hook_without_embeddings_keeps_its_shape() -> None:
    normalized = normalize_element_attachments(
        {
            "type": "scenario",
            "after": [{"match": {"location": "features/environment.py:1"}}, "junk"],
        }
    )
    assert normalized["after"] == [{"match": {"location": "features/environment.py:1"}}]


# --------------------------------------------------------------------------- #
# The template guard, held to the authority's verdict
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def lightbox_macros() -> Any:
    """The shared partial's macros, rendered through a real Jinja environment.

    ``StrictUndefined`` and autoescaping are both on, matching what the writers
    build, so a macro that reached for a missing value or emitted unescaped
    text would fail here rather than in a generated page.
    """
    env = Environment(
        loader=FileSystemLoader(str(templates_dir())),
        undefined=StrictUndefined,
        autoescape=select_autoescape(default_for_string=True, default=True),
    )
    return env.get_template("partials/lightbox.html").module


def _template_accepts(macros: Any, payload: object, media_type: str = "image/png") -> bool:
    rendered = str(macros.screenshot({"mime_type": media_type, "data": payload}))
    return rendered.strip() != ""


def test_template_guard_never_rejects_what_the_authority_accepts(
    lightbox_macros: Any,
) -> None:
    """The implication that makes the template guard defence in depth.

    The two are **not** equivalent and must not be asserted to be: the
    authority decodes the payload and walks the image, and a Jinja template
    can do neither, so the guard is a strictly weaker rule over the encoded
    characters.  What has to hold is the implication, in both of its useful
    directions:

    * **accepted by the authority implies accepted by the guard.**  A guard
      that rejected a valid attachment would silently delete failure evidence
      from a report -- the expensive failure, because nothing in the page says
      an image was dropped.
    * **rejected by the guard implies rejected by the authority.**  A guard
      that accepted something the authority refuses would be the only thing
      standing between a result-chosen payload and a ``data:`` URI, and the
      whole point of the redesign is that it never is.

    Between those sits the set of payloads the guard cannot see through --
    every member of it structurally defective but correctly encoded.  That set
    is asserted to be exactly the structural family, so the guard's weakness
    is *stated* rather than discovered later, and a future change that widened
    it would fail here.
    """
    corpus = _corpus()
    # Family sizes rather than a round number, so the floor states what the
    # corpus must actually contain: every padding class of a valid payload,
    # every pad-bit spelling of each padded one, every wrong value of
    # signature bytes 7 and 8, the hand-chosen malformed shapes, and one
    # payload per structural defect.
    assert len(_accepted_payloads()) >= 10
    assert len(_pad_bit_perturbations()) >= 50
    assert len(_wrong_signature_payloads()) >= 8
    assert len(_malformed_payloads()) >= 15
    assert len(_structural_defect_payloads()) >= 20
    assert len(corpus) == len(_accepted_payloads()) + len(_rejected_payloads())

    authority_accepts = {
        payload for payload in corpus if decode_png_payload(payload) is not None
    }
    guard_accepts = {
        payload for payload in corpus if _template_accepts(lightbox_macros, payload)
    }

    # Neither side has degenerated into accepting nothing or everything.
    assert authority_accepts == set(_accepted_payloads())
    assert 0 < len(guard_accepts) < len(corpus)

    # The implication, both ways round.
    assert authority_accepts <= guard_accepts, "the guard dropped a valid attachment"
    for payload in guard_accepts - authority_accepts:
        assert decode_png_payload(payload) is None, payload

    # And the gap between them is exactly the family a character test cannot
    # see: correctly encoded base64 of a defective image.
    structural = set(_structural_defect_encodings())
    tolerated = guard_accepts - authority_accepts
    assert tolerated <= structural, sorted(tolerated - structural)
    # The signature-only payload is NOT tolerated: it is the case the guard was
    # widened to catch, and it is in neither accepting set.
    assert SIGNATURE_ONLY_PAYLOAD not in guard_accepts
    assert SIGNATURE_ONLY_PAYLOAD not in authority_accepts


def _assignments(source: str, pattern: str) -> list[str]:
    """Every value assigned to a name matching ``pattern`` in ``source``."""
    return [match.group(1).strip() for match in re.finditer(pattern, source)]


def test_the_template_constants_are_the_authoritys_own_values() -> None:
    """A bound cannot be raised in the validator and left behind in the guard.

    The template is a different language and cannot import a Python constant,
    so the value is written twice and this is what keeps the two spellings one
    number.  Before the redesign the template carried 44739244 -- the encoded
    form of a 32 MiB ceiling that no longer exists anywhere else -- which is
    precisely the drift this asserts against.
    """
    source = (templates_dir() / "partials" / "lightbox.html").read_text(
        encoding="utf-8"
    )

    header = _assignments(source, r"set PNG_BASE64_HEADER = '([^']*)'")
    minimum = _assignments(source, r"set MIN_BASE64_CHARS = (\d+)")
    maximum = _assignments(source, r"set MAX_BASE64_CHARS = (\d+)")

    assert header == [PNG_BASE64_HEADER]
    assert maximum == [str(MAX_EMBEDDING_BASE64_CHARS)]
    # The floor is the encoded length of the smallest structurally complete
    # PNG, which is a whole number of base64 quanta.
    assert minimum == [str(((MIN_PNG_BYTES + 2) // 3) * 4)]
    assert int(minimum[0]) == 76

    # The eleven-character signature prefix is gone from the guard: it is the
    # weaker constant, and leaving it in place is how a guard silently keeps
    # applying the rule it was supposed to have replaced.
    assert "PNG_BASE64_SIGNATURE" not in source
    assert "44739244" not in source


def test_the_report_script_constants_are_the_authoritys_own_values() -> None:
    """The same pinning for ``report.js``, which also cannot import a constant.

    The script decodes with ``atob`` and so compares *bytes*, but it has no
    ``zlib``: it checks the sixteen-byte opening header and the twelve-byte
    ``IEND`` trailer, which is the most a browser-side guard can do without
    reimplementing the format.  Its two length bounds must still be the
    authority's.
    """
    source = (static_dir() / "js" / "report.js").read_text(encoding="utf-8")

    assert _assignments(source, r"var MAX_BASE64_CHARS = (\d+);") == [
        str(MAX_EMBEDDING_BASE64_CHARS)
    ]
    assert _assignments(source, r"var MIN_BASE64_CHARS = (\d+);") == [
        str(((MIN_PNG_BYTES + 2) // 3) * 4)
    ]
    assert "44739244" not in source

    # The header and trailer arrays, read out of the source and compared with
    # the bytes every valid PNG actually begins and ends with.
    header = re.search(r"var PNG_HEADER_BYTES = \[(.*?)\];", source, re.DOTALL)
    trailer = re.search(r"var PNG_TRAILER_BYTES = \[(.*?)\];", source, re.DOTALL)
    assert header is not None and trailer is not None
    header_bytes = bytes(int(value, 16) for value in header.group(1).replace("\n", "").split(",") if value.strip())
    trailer_bytes = bytes(int(value, 16) for value in trailer.group(1).replace("\n", "").split(",") if value.strip())

    assert header_bytes == PNG_SIGNATURE + (13).to_bytes(4, "big") + b"IHDR"
    assert trailer_bytes == IEND_CHUNK
    for payload in [*_real_world_pngs(), VALID_PNG, _png(9, 9, interlace=1)]:
        assert payload.startswith(header_bytes)
        assert payload.endswith(trailer_bytes)

    # The signature-only payload the review named fails the script's header
    # test by construction: sixteen bytes cannot be found in eight.
    assert len(PNG_SIGNATURE) < len(header_bytes)


def test_template_guard_rejects_every_media_type_but_png(lightbox_macros: Any) -> None:
    for media_type in ("image/svg+xml", "image/jpeg", "image/png;charset=x", "image/pngx", ""):
        assert not _template_accepts(lightbox_macros, VALID_PAYLOAD, media_type), media_type
    assert _template_accepts(lightbox_macros, VALID_PAYLOAD, "IMAGE/PNG")


def test_template_emits_a_literal_png_media_type(lightbox_macros: Any) -> None:
    """The media type in the emitted URI is a template literal, not the input's.

    Proven by the one input whose media type differs in spelling from what
    comes out: the URI says ``image/png`` although the attachment said
    ``IMAGE/PNG``.
    """
    rendered = str(
        lightbox_macros.screenshot({"mime_type": "IMAGE/PNG", "data": VALID_PAYLOAD})
    )
    assert f'src="data:{DEFAULT_MIME_TYPE};base64,{VALID_PAYLOAD}"' in rendered
    assert "IMAGE/PNG" not in rendered


def test_template_renders_nothing_at_all_for_a_rejected_attachment(
    lightbox_macros: Any,
) -> None:
    """A rejection emits no figure, no button and no broken image element."""
    for payload in ("iVBORw0KAAAA", "iVBORw0KAB==", "", None):
        rendered = str(lightbox_macros.screenshot({"mime_type": "image/png", "data": payload}))
        assert rendered.strip() == ""
        assert "<img" not in rendered
        assert "<figure" not in rendered
        assert "<button" not in rendered


def test_template_trigger_is_a_real_button_without_deferred_loading(
    lightbox_macros: Any,
) -> None:
    """The accepted case's markup, since a guard is useless if nothing renders.

    ``loading="lazy"`` is absent deliberately: the bytes are already in the
    document, so deferring the load saves nothing and costs an
    explicit-dimensions warning for a box the browser cannot predict.
    """
    rendered = str(
        lightbox_macros.screenshot(
            {"mime_type": "image/png", "data": VALID_PAYLOAD, "name": "a capture"}
        )
    )
    assert '<button type="button"' in rendered
    assert "data-report-screenshot" in rendered
    assert 'aria-label="Enlarge screenshot: a capture"' in rendered
    assert 'alt="a capture"' in rendered
    assert "<figcaption>a capture</figcaption>" in rendered
    assert 'loading="lazy"' not in rendered
    assert 'decoding="async"' in rendered
