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

``app/reporting/screenshots.py`` is the authority that settles both, and
``app/templates/partials/lightbox.html`` re-derives the same verdict without
being able to decode anything.  The tests below pin four properties:

1. the authority's rule set, clause by clause, with a case per clause;
2. that the two HTML writers put every attachment through it, so no template
   of either writer can be reached by one that has not been validated;
3. that the template guard reaches the **same verdict as the authority** on a
   generated corpus, rather than merely rejecting the two payloads a review
   happened to name -- the two implementations are independent and only an
   equivalence test keeps them from drifting;
4. that the media type a page receives is a literal of this project's and
   never a value from the input.

The corpus is generated rather than listed, because the interesting cases are
families: every padding class of a valid PNG, every single-character
perturbation of the pad bits in each class, and every wrong value of signature
bytes 7 and 8.  A hand-written list covers the case its author thought of.
"""

from __future__ import annotations

import base64
import re
from typing import Any

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.reporting.html_report import (
    decorated_features,
    emitted_features,
    render_html_report,
)
from app.reporting.pretty_reports import render_pretty_pages
from app.reporting.screenshots import (
    DEFAULT_MIME_TYPE,
    MAX_EMBEDDING_BASE64_CHARS,
    MAX_EMBEDDING_BYTES,
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
)
from app.utils.paths import templates_dir

BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

#: A PNG that is structurally plausible: the signature, then the length and
#: type of the IHDR chunk every PNG opens with, then filler.  Nothing decodes
#: it as an image -- the contract is about the signature and the encoding, and
#: no imaging dependency exists in this project to decode with.
VALID_PNG = PNG_SIGNATURE + b"\x00\x00\x00\rIHDR" + b"the rest of the file"
VALID_PAYLOAD = base64.b64encode(VALID_PNG).decode("ascii")


# --------------------------------------------------------------------------- #
# Corpus construction
# --------------------------------------------------------------------------- #


def _valid_payloads() -> list[str]:
    """One valid payload per padding class, several times over.

    A PNG's encoded length mod 4 is fixed by its byte length mod 3, so all
    three padding classes are reached by varying the file length by one byte.
    """
    return [
        base64.b64encode(PNG_SIGNATURE + b"\x00\x00\x00\rIHDR" + bytes(range(extra)))
        .decode("ascii")
        for extra in range(9)
    ]


#: Canonical base64 of exactly the eight signature bytes and nothing else: a
#: file no browser can decode as an image, but a genuinely PNG-signed one, and
#: the contract is about the signature and the encoding.  It belongs with the
#: accepted payloads, not the malformed ones -- a rejection here would mean the
#: guard was testing something other than what it claims to test.
SIGNATURE_ONLY_PAYLOAD = base64.b64encode(PNG_SIGNATURE).decode("ascii")


def _accepted_payloads() -> list[str]:
    return [*_valid_payloads(), SIGNATURE_ONLY_PAYLOAD]


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
    """Hand-chosen shapes, each naming the clause it violates."""
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
    ]


def _rejected_payloads() -> list[str]:
    return _pad_bit_perturbations() + _wrong_signature_payloads() + _malformed_payloads()


def _corpus() -> list[str]:
    return _accepted_payloads() + _rejected_payloads()


# --------------------------------------------------------------------------- #
# The authority's rule set
# --------------------------------------------------------------------------- #


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


def test_is_png_bytes_requires_all_eight_signature_bytes() -> None:
    assert is_png_bytes(VALID_PNG)
    assert not is_png_bytes(PNG_SIGNATURE[:6] + b"\x00\x00tail")
    assert not is_png_bytes(PNG_SIGNATURE[:7])
    assert not is_png_bytes(b"")
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


def test_decode_accepts_a_file_that_is_only_the_signature() -> None:
    """Degenerate, but PNG-signed and canonically encoded, so it qualifies.

    A browser renders nothing for it, which is harmless: the media type is
    fixed to ``image/png``, so the bytes are treated as a PNG and fail to
    decode.  No length floor is imposed, because the contract's security
    property is the fixed media type plus the signature, and a floor would be
    a rule the template guard would have to mirror for no gain.
    """
    decoded = decode_png_payload(SIGNATURE_ONLY_PAYLOAD)
    assert decoded == PNG_SIGNATURE


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
    second = base64.b64encode(PNG_SIGNATURE + b"\x00\x00\x00\rIHDRsecond").decode("ascii")
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
    assert capture_png(_Driver(VALID_PNG)) == VALID_PNG
    assert capture_png(_Driver(b"\xff\xd8\xff\xe0JFIF")) is None
    assert capture_png(_Driver(b"<html>an error page</html>")) is None
    assert capture_png(_Driver(PNG_SIGNATURE[:7])) is None
    assert capture_png(_Driver(b"")) is None
    assert capture_png(_Driver(None)) is None


def test_capture_png_never_raises_on_a_dead_session() -> None:
    class _Dead:
        def get_screenshot_as_png(self) -> bytes:
            raise RuntimeError("session deleted")

    assert capture_png(_Dead()) is None
    assert capture_png(object()) is None


def test_build_embedding_rejects_a_non_png_and_a_foreign_media_type() -> None:
    assert build_embedding(VALID_PNG, "s")["mime_type"] == DEFAULT_MIME_TYPE
    assert build_embedding(VALID_PNG, "s")["data"] == VALID_PAYLOAD
    with pytest.raises(ValueError, match="eight-byte signature"):
        build_embedding(b"\xff\xd8jpeg", "s")
    with pytest.raises(ValueError, match="media type"):
        build_embedding(VALID_PNG, "s", mime_type="image/svg+xml")


def test_build_embedding_omits_an_absent_name() -> None:
    assert "name" not in build_embedding(VALID_PNG, None)


# --------------------------------------------------------------------------- #
# The two writers
# --------------------------------------------------------------------------- #


def _hostile_document() -> dict[str, Any]:
    """A one-feature result set whose failed scenario carries five attachments.

    Four are hostile in a different way and one is a genuine PNG, so a writer
    that dropped validation would show four of them and a writer that dropped
    the attachment support entirely would show none -- the two failure modes
    are distinguishable by the assertions below.
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
FORBIDDEN_URIS_IN_PAGES = (
    "data:image/svg+xml;base64",
    "data:image/jpeg",
    "data:image/png;base64,iVBORw0KAAAA",
    "data:image/png;base64,iVBORw0KAB==",
)

#: Probes that identify a *rejected* attachment by its name.  None of these
#: strings occurs in either shared asset, so they may be tested bare.
FORBIDDEN_NAMES_IN_PAGES = (
    "onload=",
    "svg-attachment",
    "wrong-signature",
    "noncanonical-pad-bits",
    "jpeg-media-type",
)

FORBIDDEN_IN_PAGES = FORBIDDEN_URIS_IN_PAGES + FORBIDDEN_NAMES_IN_PAGES


def test_html_writer_validates_attachments_before_the_template_sees_them() -> None:
    document = _hostile_document()
    decorated = decorated_features(emitted_features(document))
    surviving = decorated[0]["elements"][0]["after"][0]["embeddings"]
    assert [item["name"] for item in surviving] == ["the one good png"]
    assert {item["mime_type"] for item in surviving} == {DEFAULT_MIME_TYPE}


def test_html_writer_does_not_mutate_the_shared_result_set() -> None:
    """Four writers are fed from one merged set; a writer that edited it would
    change what the other three see."""
    document = _hostile_document()
    before = document["features"][0]["elements"][0]["after"][0]["embeddings"]
    decorated_features(emitted_features(document))
    after = document["features"][0]["elements"][0]["after"][0]["embeddings"]
    assert after is before
    assert len(after) == 5


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


def test_template_guard_agrees_with_the_authority_on_the_whole_corpus(
    lightbox_macros: Any,
) -> None:
    """The equivalence test.

    The template cannot decode base64 and the authority does nothing else, so
    the two are independent implementations of one rule.  Only checking them
    against each other over families of inputs keeps them from drifting -- and
    the counts are asserted as well, so a guard that had degenerated into
    accepting nothing, or into accepting everything, could not pass.
    """
    corpus = _corpus()
    # Family sizes rather than a round number, so the floor states what the
    # corpus must actually contain: every padding class of a valid payload,
    # every pad-bit spelling of each padded one, every wrong value of
    # signature bytes 7 and 8, and the hand-chosen malformed shapes.
    assert len(_accepted_payloads()) >= 10
    assert len(_pad_bit_perturbations()) >= 50
    assert len(_wrong_signature_payloads()) >= 8
    assert len(_malformed_payloads()) >= 15
    assert len(corpus) == len(_accepted_payloads()) + len(_rejected_payloads())

    disagreements = [
        payload
        for payload in corpus
        if _template_accepts(lightbox_macros, payload)
        != (decode_png_payload(payload) is not None)
    ]
    assert disagreements == []

    # The families are asserted individually as well, so a guard that had
    # degenerated into accepting nothing -- or everything -- could not pass by
    # agreeing with an equally degenerate sibling.
    for payload in _accepted_payloads():
        assert decode_png_payload(payload) is not None, payload
        assert _template_accepts(lightbox_macros, payload), payload
    for payload in _rejected_payloads():
        assert decode_png_payload(payload) is None, payload
        assert not _template_accepts(lightbox_macros, payload), payload

    accepted = sum(1 for payload in corpus if decode_png_payload(payload) is not None)
    assert accepted == len(_accepted_payloads())
    assert 0 < accepted < len(corpus)


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
