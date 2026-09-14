"""Failure-screenshot contract: the four helpers and the hook that uses them.

Subjects
--------
``app/reporting/screenshots.py``
    :func:`~app.reporting.screenshots.capture_png`,
    :func:`~app.reporting.screenshots.encode_png`,
    :func:`~app.reporting.screenshots.build_embedding` and
    :func:`~app.reporting.screenshots.capture_failure_embedding`, driven as pure
    units against the duck-typed recorder in ``tests/conftest.py`` -- the
    capture-and-attach half of the module, plus its exported surface and its
    import and filesystem boundaries.  The validation half it also owns
    (:func:`~app.reporting.screenshots.decode_png_payload`,
    :func:`~app.reporting.screenshots.canonical_png_payload` and the five
    ``normalize_*`` functions, which guard a *result-controlled* attachment on
    its way to a template) is the subject of
    ``tests/test_png_embedding_contract.py``; the two meet at
    :func:`~app.reporting.screenshots.is_png_bytes`, the one definition of "is
    a PNG" in the project, which both assert against.

``features/environment.py``
    ``after_scenario``, driven at integration level: the real
    :func:`~app.reporting.screenshots.capture_png` runs against that same
    recorder, and only the session teardown is substituted, because quitting a
    driver is the one thing in the function that has no observable stand-in of
    its own.

The Java anchor
---------------
Both subjects port eight lines of ``Hooks.java`` -- ``Hooks.java:11-18`` at the
pinned reference revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``::

    @After                                                       // :11
    public void teardownScenario(Scenario scenario){
        if(scenario.isFailed()){                                 // :13
            byte [] screenshot = ((TakesScreenshot) Driver.getDriver())
                    .getScreenshotAs(OutputType.BYTES);          // :14
            scenario.attach(screenshot, "image/png", scenario.getName()); // :15
        }
        Driver.closeDriver();                                    // :17 - OUTSIDE the if
    }

**That hook never ran.**  ``Hooks.java:5`` imports ``@After`` from
``org.junit.After`` instead of ``io.cucumber.java.After``, so Cucumber never
registered it -- corroborated by the committed ``target/cucumber.json``, which
carries a failed scenario with no ``embeddings`` and no ``"after"`` key
anywhere.  AAP Conflict 6 corrects that defect rather than reproducing it
(deviation 6: *"the teardown hook is registered as a real hook, enabling
failure screenshots and per-scenario driver teardown"*), because reproducing it
would ship the screenshot capability dead on arrival and leak one browser per
scenario.  Consequently **no observed artifact pins the embedding shape**: it
is derived from the ``attach`` call plus the JSON generator's own
``createEmbeddingMap``, and AAP §0.6 states it.  This module is therefore the
specification's only executable form, which is why it asserts the shape key by
key rather than against a golden file.

What AAP §0.6 fixes, and this module pins
-----------------------------------------
* Capture happens **on failure only, exactly once, before the driver is quit**.
  There is no else-branch in the source, so there is no passing-scenario path
  and no enable flag; the configuration surface stays at its six keys.
* The embedding is ``{"mime_type": "image/png", "data": "<base64 PNG>",
  "name": "<scenario name>"}``.  The underscore spelling of ``mime_type`` is the
  contract and not a typo -- it is the field name the JSON generator emits and
  the one every downstream reader, including the Cucumber publisher, expects.
* A capture failure -- a dead session, an object that cannot be photographed,
  an unusable payload -- is **logged and suppressed**: no embedding is emitted
  and the scenario's status is unchanged.  That suppression is AAP deviation 19,
  and the reason it is safe is the reason it is mandatory: a screenshot is
  evidence about a result, never part of one.
* The payload is **a PNG or it is not evidence**.  An attachment reaches both
  HTML writers as result-controlled data and is inlined into a document a human
  opens, so the module owns the one strict test every consumer applies: the
  media type is *exactly* ``image/png``, the bytes begin with PNG's complete
  eight-byte signature, and the image is within
  :data:`~app.reporting.screenshots.MAX_EMBEDDING_BYTES`.  A payload failing it
  is refused rather than repaired -- ``capture_png`` logs and answers ``None``,
  ``build_embedding`` raises ``ValueError`` -- and the refusal costs the
  evidence and nothing else.
* Beyond the signature the content stays **opaque**.  The source attaches
  whatever ``getScreenshotAs(OutputType.BYTES)`` returned and its POM declares
  no imaging dependency, so a PNG whose compressed image data no longer decodes
  is carried through byte-exactly, with no format sniff, no dimension test, no
  re-encode and no log record.  Both halves are asserted below, and neither
  means anything without the other.
* A suppressed capture **says which scenario lost its evidence**.  Both capture
  entry points take a keyword-only ``scenario_id``; it is rendered with ``%r``
  into the suppression record and reaches nothing else -- not the embedding, not
  an artifact -- and omitting it leaves the record reading exactly as it did
  before identities existed.  Asserted on the paths whose record is composed by
  the module's own ``_suppression_record`` helper: the dead session, the driver
  that cannot be photographed, and the unusable payload.  The signature and
  size refusals build their record with ``logger.error`` directly and so carry
  no identity segment; the refusal tests below assert the record those paths do
  emit rather than the one they should.

Raw bytes at the hook, base64 in the artifact
---------------------------------------------
The distinction is load-bearing and is asserted in both directions.
``after_scenario`` hands behave's ``Context.attach`` the **raw PNG bytes**,
because behave documents a bytes-like payload and its own JSON formatter
base64-encodes whatever it is handed -- a pre-encoded string would be encoded
twice.  :func:`~app.reporting.screenshots.build_embedding` is the other side of
that seam: it is what the port's own writers serialise, and it base64-encodes
exactly once, unchunked, so that the ``data:<mime_type>;base64,<data>`` URI both
HTML outputs build from it cannot be corrupted by a line break.

Standing constraints
--------------------
No browser, no network, no driver download and no disk write: the driver is
``tests/conftest.py``'s :class:`~conftest.StubDriver`, which imports no
selenium at all.  That is not merely convenient -- it is what makes the
duck-typing invariant checkable rather than asserted, since
``app/reporting/screenshots.py`` may not import the Selenium bindings and does
not.
"""

from __future__ import annotations

import ast
import base64
import importlib.util
import logging
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from conftest import DEFAULT_SCREENSHOT_PNG, FakeContext, StubDriver

from app.reporting import screenshots
from app.reporting.screenshots import (
    CAPTURE_FAILURE_MESSAGE,
    DEFAULT_MIME_TYPE,
    build_embedding,
    capture_failure_embedding,
    capture_png,
    encode_png,
)

# --------------------------------------------------------------------------- #
# Fixed names and locations
# --------------------------------------------------------------------------- #

#: Repository root, from this file's own position: ``tests/`` -> root.
REPO_ROOT: Path = Path(__file__).resolve().parents[1]

#: ``features/environment.py``'s path.  There is deliberately no
#: ``features/__init__.py`` -- behave discovers the file by path from the
#: directory named in ``behave.ini`` -- so the hook module is loaded from this
#: location rather than imported by dotted name.
ENVIRONMENT_PATH: Path = REPO_ROOT / "features" / "environment.py"

#: Name the loaded hook module is registered under.  Distinct from any dotted
#: name the application uses, so nothing in the port can accidentally resolve
#: it as a package module.
HOOK_MODULE_NAME: str = "features_environment_under_test"

#: The logger every capture failure must reach.  Asserted by name because AAP
#: §0.6 requires the failure to be *greppable*: an operator reading a CI log
#: has to be able to find it, and the module that owns the behaviour is the one
#: that must own the record.
SCREENSHOTS_LOGGER: str = "app.reporting.screenshots"

#: The exported surface of the module under test, in the order it declares.
#: Four groups, and the module is the authority for all four: the two messages
#: and the five constants a consumer asserts against instead of restating; the
#: four capture-and-embed helpers that port ``Hooks.java:14-15``; the three
#: payload-level validators (:func:`is_png_bytes`,
#: :func:`decode_png_payload`, :func:`canonical_png_payload`); and the five
#: normalizers through which a result-controlled attachment reaches a template.
#: The private ``_TakesScreenshot`` protocol and the private
#: ``_suppression_record`` helper are deliberately absent -- neither has a
#: runtime role outside this module.
EXPECTED_EXPORTS: list[str] = [
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

#: Every module ``app/reporting/screenshots.py`` is permitted to import.  The
#: standard library only: the reporting package's single outgoing intra-package
#: edge in the AAP dependency graph is to ``app/utils``, and this module needs
#: nothing even from there.  ``base64`` and ``binascii`` are the embedding
#: contract's own decoder -- strict base64 decoding raises ``binascii.Error``,
#: which has to be named to be caught -- and ``collections.abc`` supplies the
#: ``Mapping``/``Sequence`` tests the normalizers apply to result-controlled
#: containers.  No imaging distribution appears, and none may: the source POM
#: declares none.
EXPECTED_IMPORTS: frozenset[str] = frozenset(
    {"__future__", "base64", "binascii", "collections.abc", "logging", "typing"}
)

#: Names whose presence would mean this module reaches the filesystem.  AAP §0.6
#: requires the embedding to be inline base64: no path module defines a
#: screenshot location, and writing one would break the self-contained
#: guarantee of ``target/cucumber-reports.html``.
FILESYSTEM_NAMES: frozenset[str] = frozenset(
    {
        "NamedTemporaryFile",
        "Path",
        "TemporaryDirectory",
        "io",
        "makedirs",
        "mkdir",
        "mkstemp",
        "open",
        "os",
        "pathlib",
        "shutil",
        "tempfile",
        "unlink",
        "write",
        "write_bytes",
        "write_text",
    }
)

#: A payload long enough that ``base64.encodebytes`` would wrap it: that encoder
#: emits a newline every 76 output characters, which is every 57 input bytes.
#: 512 bytes is comfortably past the first wrap and past the second, so a single
#: "no newline" assertion over it is decisive rather than incidental.
LONG_PAYLOAD: bytes = bytes(range(256)) * 2

#: A scenario name carrying every character class escaping could disturb: a
#: quotation mark, an apostrophe, a colon, an ampersand, a backslash and a
#: non-ASCII letter.  ``build_embedding`` must carry it through untouched --
#: escaping belongs to the JSON or HTML consumer that serialises it, and a name
#: mangled here would be mangled in every artifact at once.
HOSTILE_SCENARIO_NAME: str = 'Le "devis" de l\'été: CRM & Sales \\ 100%'

#: Names whose presence would mean the module inspects a payload *beyond* the
#: one test the embedding contract fixes.  That contract is a signature test and
#: a size bound and stops there: AAP §0.6 fixes a no-imaging, no-transform
#: design -- the source attaches whatever ``getScreenshotAs(OutputType.BYTES)``
#: returned and the source POM declares no imaging dependency -- so a format
#: sniff, a chunk walk or a dimension test would each be an addition the parity
#: obligation forbids.  ``startswith`` is deliberately *not* here: it is how the
#: sanctioned signature test is spelled, and the module-surface test below
#: asserts that it is present as well as alone.  Kept to vocabulary that can
#: only mean further inspection, so that an unrelated edit to the module cannot
#: trip it.
BEYOND_SIGNATURE_NAMES: frozenset[str] = frozenset(
    {
        "Image",
        "PIL",
        "endswith",
        "fromhex",
        "hexlify",
        "imghdr",
        "magic",
        "removeprefix",
        "removesuffix",
        "struct",
        "unpack",
        "unpack_from",
        "what",
    }
)

#: The parameter names that carry screenshot bytes through the module.  A slice
#: or an index of one of them is how a hand-rolled header check is spelled
#: without a library, so their absence from every subscript is what keeps the
#: signature test the exported constant's business rather than an offset
#: comparison someone wrote twice.
PAYLOAD_PARAMETER_NAMES: frozenset[str] = frozenset({"png"})

#: Every numeric literal the module is permitted to contain, by the top-level
#: declaration or function that owns it.  Three owners, and each number in them
#: is arithmetic the contract states rather than a fact about an image:
#:
#: * ``MAX_EMBEDDING_BYTES`` -- 32 MiB, spelled ``32 * 1024 * 1024``;
#: * ``MAX_EMBEDDING_BASE64_CHARS`` -- the encoded length that bound implies,
#:   base64 emitting four characters per three bytes rounded up;
#: * ``decode_png_payload`` -- base64's four-character quantum, which an
#:   encoded length must be a positive multiple of.
#:
#: Nothing on the image path may carry a number at all: no offset, no
#: dimension, no minimum size.  A magic number written anywhere else fails the
#: content test below and is reported with its owner's name.
EXPECTED_NUMERIC_LITERALS: dict[str, list[int | float]] = {
    "MAX_EMBEDDING_BYTES": [32, 1024],
    "MAX_EMBEDDING_BASE64_CHARS": [2, 3, 4],
    "decode_png_payload": [0, 4],
}

#: The eight-byte PNG signature, spelled here independently of the module under
#: test and asserted equal to its exported :data:`PNG_SIGNATURE` by
#: :func:`test_the_exported_signature_is_pngs_own_eight_bytes`.  Spelling it
#: twice is the point: every payload below is measured against this literal, so
#: the corpus's own premise -- these bytes are, or are not, PNG-signed -- is
#: pinned rather than inherited from the constant it is used to check.
PNG_SIGNATURE: bytes = b"\x89PNG\r\n\x1a\n"


def flip_byte(payload: bytes, index: int) -> bytes:
    """Return *payload* with the byte at *index* inverted.

    XOR with ``0xFF`` rather than assignment of a fixed value, so the result
    differs from the input whatever the original byte was: a "corruption" that
    happened to write the byte already there would leave its case asserting
    nothing.

    :param payload: The bytes to corrupt.
    :param index: Position of the byte to invert.
    :returns: A new ``bytes`` of the same length, differing at *index* only.
    """
    return payload[:index] + bytes((payload[index] ^ 0xFF,)) + payload[index + 1 :]


#: The first four bytes of a real PNG signature and nothing else -- a capture
#: cut off mid-header, which is the shape a dying session's partial response
#: takes.
TRUNCATED_PNG: bytes = PNG_SIGNATURE[:4]

#: The 1x1 PNG with its fourth signature byte inverted: structurally a PNG right
#: up to the byte where it stops being one.  The case a six-byte or a
#: prefix-only signature test would let through, which is why it is a case of
#: its own rather than folded into the arbitrary-bytes ones.
CORRUPTED_SIGNATURE_PNG: bytes = flip_byte(DEFAULT_SCREENSHOT_PNG, 3)

#: The 1x1 PNG with a byte inverted inside its compressed image data: the
#: signature still identifies it as a PNG, the pixels no longer decode.  The one
#: payload in this corpus that must be **carried**, and the case that catches a
#: *transforming* or *validating* change -- a re-encode, a copy through an
#: imaging library or a CRC check would each repair or reject it, and
#: byte-exactness would fail.
CORRUPTED_BODY_PNG: bytes = flip_byte(DEFAULT_SCREENSHOT_PNG, len(DEFAULT_SCREENSHOT_PNG) // 2)

#: Bytes that could not be an image in any format -- a short ASCII payload, the
#: shape an HTTP error page substituted for a screenshot response would have.
NON_IMAGE_TEXT: bytes = b"<html>not a screenshot</html>"

#: A JPEG start-of-image marker: the right shape for an image, the wrong format
#: for the ``image/png`` MIME type the attachment declares.  High bytes rather
#: than ASCII, so a payload that is neither text nor PNG is covered too.
JPEG_HEADER: bytes = b"\xff\xd8\xff\xe0"

#: The smallest possible non-empty payload.  Pins that the refusal is about the
#: signature rather than about a minimum size: it is refused for the same reason
#: the JPEG marker is, and the record says so.
SINGLE_BYTE: bytes = b"\x00"

#: PNG's first **six** signature bytes followed by two that are not its
#: seventh and eighth, then filler so the payload is not refused for its
#: length.  The payload a six-byte test accepts and an eight-byte test refuses,
#: and therefore the one case that distinguishes them behaviourally: base64's
#: ``iVBORw0K`` prefix fixes only these six bytes, so a guard written against
#: that prefix admits a byte string that is not a PNG at all.
SIX_BYTE_PREFIX_PAYLOAD: bytes = PNG_SIGNATURE[:6] + b"\x00\x00" + b"\x00" * 16

#: Every payload the module must **refuse**, one reported case each: none of
#: them begins with PNG's eight-byte signature, so none of them may reach a
#: ``data:`` URI in a document a human opens.  Shared by the four refusal tests
#: below so that a payload added here is immediately driven through the unit
#: helpers, the composed entry point and the hook.
REFUSED_PAYLOADS: list[Any] = [
    pytest.param(TRUNCATED_PNG, id="truncated-png-header"),
    pytest.param(CORRUPTED_SIGNATURE_PNG, id="corrupted-png-signature"),
    pytest.param(SIX_BYTE_PREFIX_PAYLOAD, id="six-byte-prefix-only"),
    pytest.param(NON_IMAGE_TEXT, id="non-image-text"),
    pytest.param(JPEG_HEADER, id="jpeg-header"),
    pytest.param(SINGLE_BYTE, id="single-byte"),
]

#: Every payload the module must **carry byte-exactly**: both are PNG-signed, so
#: both satisfy the whole of the contract, and one of them is not a decodable
#: image at all.  Driven through the same four levels as the refused corpus,
#: because "refuses what is not PNG" and "judges nothing else" are one contract
#: and neither half means anything alone.
CARRIED_PAYLOADS: list[Any] = [
    pytest.param(DEFAULT_SCREENSHOT_PNG, id="pristine-png"),
    pytest.param(CORRUPTED_BODY_PNG, id="corrupted-png-body"),
]

#: A diagnostic scenario identity of the shape ``features/environment.py``
#: builds -- feature file, line and quoted scenario name -- carrying a newline
#: and a quotation mark, the characters that would break a log line apart if the
#: module interpolated it with ``%s`` instead of ``%r``.
SCENARIO_IDENTITY: str = "features/Crm.feature:9 'User can create\na \"pipeline\"'"


# --------------------------------------------------------------------------- #
# Stand-ins this module owns
#
# ``tests/conftest.py`` supplies the driver and the behave context; behave's
# *scenario* has no stand-in there, because ``after_scenario`` is the only
# caller in the port that reads one.  The two classes below are that stand-in,
# and they are recorders rather than data holders for one reason: AAP §0.6's
# "the scenario's status is unchanged" is only an assertion if a write would be
# visible.  Both record every attribute assignment and then apply it, so the
# objects stay usable while the attempt is preserved for inspection.
# --------------------------------------------------------------------------- #


class RecordingStatus:
    """behave ``Status`` stand-in exposing ``has_failed()`` and recording writes.

    behave's real ``Status`` is an enum whose ``has_failed()`` unions ``failed``,
    ``error``, ``hook_error``, ``cleanup_error``, ``undefined`` and ``pending``
    -- the analogue of Cucumber's ``Scenario.isFailed()``, which is true for an
    exception as well as an assertion failure.  Only that predicate and the
    member's name are reproduced, because they are the whole of what the hook
    reads.

    :ivar writes: Every ``(name, value)`` attribute assignment attempted on this
        object, in order.  Expected to stay empty: the hook reads the status and
        must never write it.
    :ivar has_failed_calls: How many times :meth:`has_failed` was called, so a
        test can assert the predicate is consulted once per scenario rather than
        polled.
    """

    def __init__(self, *, failed: bool) -> None:
        """Build a status reporting *failed* and recording every write.

        :param failed: What :meth:`has_failed` reports.
        """
        # ``object.__setattr__`` for the construction-time attributes: routing
        # them through ``__setattr__`` would record the object's own
        # initialisation as mutation and make every ``writes == []`` assertion
        # meaningless.
        object.__setattr__(self, "name", "failed" if failed else "passed")
        object.__setattr__(self, "writes", [])
        object.__setattr__(self, "has_failed_calls", 0)
        object.__setattr__(self, "_failed", failed)

    def has_failed(self) -> bool:
        """Report whether the scenario failed, counting the call.

        :returns: ``True`` when this status stands for a failed scenario.
        """
        object.__setattr__(self, "has_failed_calls", self.has_failed_calls + 1)
        return self._failed

    def __setattr__(self, name: str, value: Any) -> None:
        """Record an attribute assignment, then perform it.

        Recording rather than raising: a raise would change the behaviour under
        test, and the question this class answers is whether production code
        *attempts* a write, not what it would do if the write were refused.

        :param name: Attribute being assigned.
        :param value: Value assigned.
        :returns: ``None``.
        """
        self.writes.append((name, value))
        object.__setattr__(self, name, value)

    def __repr__(self) -> str:
        """Summarise the status for a failure message.

        :returns: A short representation naming the member.
        """
        return f"<RecordingStatus name={self.name!r} writes={len(self.writes)}>"


class RecordingScenario:
    """behave ``Scenario`` stand-in carrying a name and a :class:`RecordingStatus`.

    ``after_scenario`` reads exactly two things from a scenario -- ``status``,
    to decide whether evidence is gathered, and ``name``, which the result
    collector records as the attachment's name -- so exactly two things are
    reproduced.  Writes are recorded for the same reason they are on the status:
    rebinding ``scenario.status`` would be a mutation too, and it has to be
    visible to be excluded.

    :ivar writes: Every ``(name, value)`` attribute assignment attempted, in
        order.  Expected to stay empty.
    """

    def __init__(self, name: str, *, failed: bool) -> None:
        """Build a scenario with the given name and outcome.

        :param name: The scenario's name, as ``scenario.getName()`` supplies it
            in the Java original.
        :param failed: Whether the scenario failed, driving
            :meth:`RecordingStatus.has_failed`.
        """
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "status", RecordingStatus(failed=failed))
        object.__setattr__(self, "writes", [])

    def __setattr__(self, name: str, value: Any) -> None:
        """Record an attribute assignment, then perform it.

        :param name: Attribute being assigned.
        :param value: Value assigned.
        :returns: ``None``.
        """
        self.writes.append((name, value))
        object.__setattr__(self, name, value)

    def __repr__(self) -> str:
        """Summarise the scenario for a failure message.

        :returns: A short representation naming the scenario and its status.
        """
        return f"<RecordingScenario name={self.name!r} status={self.status!r}>"


class OnlyScreenshotDriver:
    """An object whose entire surface is ``get_screenshot_as_png()``.

    The duck-typing proof.  ``app/reporting/screenshots.py`` performs no
    ``isinstance`` check anywhere -- its ``_TakesScreenshot`` protocol is a type
    hint and nothing more, playing the documentary role the Java cast to
    ``TakesScreenshot`` plays -- so an object exposing that one method must be
    accepted with no Selenium type in sight.  ``__slots__`` is declared so that
    this class cannot quietly acquire a second method and weaken the proof.
    """

    __slots__ = ("calls", "payload")

    def __init__(self, payload: bytes) -> None:
        """Build a one-method driver returning *payload*.

        :param payload: Bytes handed back by :meth:`get_screenshot_as_png`.
        """
        self.payload = payload
        self.calls = 0

    def get_screenshot_as_png(self) -> bytes:
        """Return the programmed payload, counting the capture.

        :returns: The payload this object was built with.
        """
        self.calls += 1
        return self.payload


class NoScreenshotDriver:
    """A driver-shaped object that cannot be photographed.

    Stands for the two situations AAP §0.6 groups together: a session that has
    died, and the ``None``-shaped result a mis-configured ``browser`` property
    leaves behind.  It exposes an unrelated method, so a test using it proves
    that the *absence of the capture method* is what takes the suppressed-failure
    path, not the absence of a surface altogether.
    """

    __slots__ = ()

    def quit(self) -> None:
        """Do nothing, standing in for a driver's teardown.

        :returns: ``None``.
        """


class AttachFailure(RuntimeError):
    """Raised by a test's ``context.attach`` to prove teardown is unconditional.

    Distinctive on purpose: the assertion that it escaped ``after_scenario``
    must not be satisfiable by any other error, because the point of the test is
    that the browser was still quit *while* this was propagating.
    """


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def capture_failure_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Every capture-failure record ``caplog`` holds, at ERROR or above.

    Selection is by the exported :data:`CAPTURE_FAILURE_MESSAGE` constant rather
    than a duplicated literal: the constant is exported precisely so that a
    change to the wording cannot leave a test asserting a string the module no
    longer logs.

    :param caplog: pytest's log-capture fixture.
    :returns: The matching records, in the order they were emitted.
    """
    return [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and CAPTURE_FAILURE_MESSAGE in record.getMessage()
    ]


def screenshot_warning_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Every record the screenshots logger emitted at WARNING or above.

    Broader than :func:`capture_failure_records` in both directions -- any
    level from WARNING up, and any message -- because the passthrough tests
    assert that *nothing* was complained about, and a module that had grown a
    content check might well word its complaint differently from
    :data:`CAPTURE_FAILURE_MESSAGE`.  WARNING is the threshold because that is
    where the plan's stderr routing begins: a record at or above it reaches an
    operator's log and therefore represents the module judging the payload.

    :param caplog: pytest's log-capture fixture.
    :returns: The matching records, in the order they were emitted.
    """
    return [
        record
        for record in caplog.records
        if record.name == SCREENSHOTS_LOGGER and record.levelno >= logging.WARNING
    ]


def numeric_literals_by_owner(module: ast.Module) -> dict[str, list[int | float]]:
    """Every numeric literal in *module*, grouped by the declaration that owns it.

    The owner is the top-level statement the literal sits in -- a function by
    its name, an annotated or plain assignment by its target -- so a failure
    names the place a magic number appeared instead of only the number.
    ``bool`` is excluded: ``True`` and ``False`` are ``int`` subclasses in
    Python and say nothing about a payload.

    :param module: The parsed module to read.
    :returns: Owner name -> its distinct numeric literals, ascending.  Owners
        with no numeric literal are absent, so the mapping compares equal to a
        declaration of the whole permitted set.
    """
    owners: dict[str, set[int | float]] = {}

    for statement in module.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            owner = statement.name
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            owner = statement.target.id
        elif isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) for target in statement.targets
        ):
            owner = next(
                target.id for target in statement.targets if isinstance(target, ast.Name)
            )
        else:
            owner = ast.unparse(statement).splitlines()[0]

        for node in ast.walk(statement):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool)
            ):
                owners.setdefault(owner, set()).add(node.value)

    return {owner: sorted(values) for owner, values in owners.items()}


def quit_driver_recorder(driver: StubDriver) -> Callable[[], None]:
    """A ``quit_driver`` stand-in that quits *driver* into its own ordered log.

    ``app.automation.quit_driver`` closes the session held in the worker's slot,
    which is not the session a unit test publishes on its context, so calling
    the real function would record nothing and prove nothing.  Routing the
    teardown through the recorder instead puts the capture and the quit in **one
    ordered log**, which is what makes "capture happens before quit" an
    assertion about order rather than about two independent counters.

    :param driver: The recorder whose :meth:`~conftest.StubDriver.quit` stands
        for the session teardown.
    :returns: A zero-argument callable matching ``quit_driver``'s signature.
    """

    def quit_driver() -> None:
        """Quit the recorded session.

        :returns: ``None``.
        """
        driver.quit()

    return quit_driver


def raising_attach(error: BaseException) -> Callable[[str, Any], None]:
    """An ``attach`` stand-in that raises *error* instead of recording.

    :param error: The exception raised on every call.
    :returns: A two-argument callable matching behave's
        ``Context.attach(mime_type, data)``.
    """

    def attach(mime_type: str, data: Any) -> None:
        """Raise the programmed error.

        :param mime_type: Ignored; present to match behave's signature.
        :param data: Ignored; present to match behave's signature.
        :returns: Never returns.
        :raises BaseException: Always -- that is the entire purpose.
        """
        raise error

    return attach


def operation_log(driver: StubDriver) -> list[str]:
    """The operation names from a recorder's ordered log, arguments dropped.

    :param driver: The recorder to read.
    :returns: Operation names in the order they happened.
    """
    return [operation for operation, _ in driver.calls]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def hook_module() -> ModuleType:
    """``features/environment.py``, loaded once from its path.

    Loaded rather than imported: there is no ``features/__init__.py`` -- behave
    reads the file from the directory named by ``behave.ini`` -- and adding one
    to make a dotted import work would change the layout the engine depends on.
    Module-scoped because executing it imports ``app.automation`` and therefore
    the Selenium bindings, which is the one genuinely slow import in this
    module's reach and is completely deterministic once done.

    The loaded module binds ``quit_driver``, ``capture_png``, ``get_driver``,
    ``set_userdata`` and ``DEFAULT_MIME_TYPE`` at module level, so a test
    substitutes a collaborator by patching this object's attribute -- which is
    exactly how the production hook resolves them at call time.

    :returns: The executed hook module.
    """
    spec = importlib.util.spec_from_file_location(HOOK_MODULE_NAME, ENVIRONMENT_PATH)

    if spec is None or spec.loader is None:
        pytest.fail(f"features/environment.py is not loadable from {ENVIRONMENT_PATH}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def screenshots_source() -> ast.Module:
    """The parsed syntax tree of ``app/reporting/screenshots.py``.

    Parsed from ``screenshots.__file__`` rather than from a path spelled out
    here, so the assertions below cannot drift onto a stale copy of the module.
    A syntax tree rather than a text search because the two boundary
    assertions -- no configuration or automation import, no filesystem access --
    are about *code*, and a text search would be satisfied or defeated by the
    module's own prose, which discusses both at length.

    :returns: The module's AST.
    """
    source_path = Path(screenshots.__file__)
    return ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))


# --------------------------------------------------------------------------- #
# capture_png -- the port of getScreenshotAs(OutputType.BYTES) [Hooks.java:14]
# --------------------------------------------------------------------------- #


def test_capture_png_returns_the_payload_unchanged_after_exactly_one_capture(
    stub_driver: StubDriver,
) -> None:
    """A valid payload comes back unchanged, from exactly one capture.

    AAP §0.6: capture happens *once* per scenario -- one call, one image.  The
    ordered log is asserted whole, so a second call added anywhere in the
    function would fail this rather than merely inflate a counter.
    """
    result = capture_png(stub_driver)

    assert result == DEFAULT_SCREENSHOT_PNG
    assert isinstance(result, bytes)
    assert stub_driver.calls == [("get_screenshot_as_png", ())]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (bytearray(DEFAULT_SCREENSHOT_PNG), DEFAULT_SCREENSHOT_PNG),
        (memoryview(DEFAULT_SCREENSHOT_PNG), DEFAULT_SCREENSHOT_PNG),
    ],
    ids=["bytearray", "memoryview"],
)
def test_capture_png_normalises_a_bytes_like_payload(
    stub_driver: StubDriver,
    payload: Any,
    expected: bytes,
) -> None:
    """A ``bytearray`` or ``memoryview`` payload is converted to equal ``bytes``.

    Both are legitimate bytes-like returns, and both must reach
    :func:`~app.reporting.screenshots.encode_png` as ``bytes`` with identical
    content -- the normalisation is what keeps a well-behaved but unusual driver
    off the capture-failure path.
    """
    stub_driver.screenshot_png = payload

    result = capture_png(stub_driver)

    assert type(result) is bytes
    assert result == expected


@pytest.mark.parametrize(
    ("payload", "type_name"),
    [
        (b"", "bytes"),
        ("iVBORw0KGgo=", "str"),
        (42, "int"),
        (None, "NoneType"),
    ],
    ids=["empty-bytes", "str", "int", "none"],
)
def test_capture_png_reports_an_unusable_payload_as_a_capture_failure(
    stub_driver: StubDriver,
    caplog: pytest.LogCaptureFixture,
    payload: Any,
    type_name: str,
) -> None:
    """An empty or non-bytes payload yields ``None`` and one ERROR naming its type.

    A truncated or empty payload would render as a broken image in both HTML
    artifacts, which is worse than no embedding at all, so AAP §0.6's
    capture-failure path is the right one for it.  The offending type is named
    in the record because that is the only thing distinguishing this failure
    from a dead session in an operator's log.
    """
    stub_driver.screenshot_png = payload

    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        result = capture_png(stub_driver)

    assert result is None
    assert operation_log(stub_driver) == ["get_screenshot_as_png"]

    records = capture_failure_records(caplog)
    assert len(records) == 1
    assert records[0].name == SCREENSHOTS_LOGGER
    assert records[0].levelno == logging.ERROR
    assert type_name in records[0].getMessage()


def test_capture_png_accepts_any_object_exposing_the_capture_method(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An object whose only method is ``get_screenshot_as_png()`` is photographed.

    No ``isinstance`` check exists anywhere in the module -- the private
    ``_TakesScreenshot`` protocol is a type hint only -- which is what lets
    ``app/reporting/screenshots.py`` stay free of the Selenium import that only
    ``app/automation`` may make.
    """
    driver = OnlyScreenshotDriver(DEFAULT_SCREENSHOT_PNG)

    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        result = capture_png(driver)

    assert result == DEFAULT_SCREENSHOT_PNG
    assert driver.calls == 1
    assert capture_failure_records(caplog) == []


@pytest.mark.parametrize(
    "driver",
    [NoScreenshotDriver(), object(), None],
    ids=["driver-without-the-method", "bare-object", "none"],
)
def test_capture_png_suppresses_a_missing_capture_method(
    caplog: pytest.LogCaptureFixture,
    driver: Any,
) -> None:
    """An object lacking the method is logged and reported as ``None``.

    The ``AttributeError`` this raises takes the same suppressed path as a dead
    session, which is deliberate: ``get_driver()`` legitimately returns ``None``
    for an unrecognised ``browser`` value -- the Java switch has no ``default:``
    branch -- and the hook passes that value straight through without a check,
    because suppression has exactly one owner and this is it (AAP deviation 19).
    """
    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        result = capture_png(driver)

    assert result is None

    records = capture_failure_records(caplog)
    assert len(records) == 1
    assert records[0].name == SCREENSHOTS_LOGGER
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None


def test_capture_png_suppresses_a_raised_exception_and_logs_the_traceback(
    stub_driver: StubDriver,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A driver that raises yields ``None``, logged with its traceback.

    The dead-session case AAP deviation 19 names.  ``WebDriverException`` cannot
    be caught by name here without importing Selenium, so the ``except
    Exception`` is broad on purpose -- and the traceback is what preserves the
    diagnosis the narrow catch would have carried.
    """
    stub_driver.screenshot_error = RuntimeError("session died")

    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        result = capture_png(stub_driver)

    assert result is None
    assert operation_log(stub_driver) == ["get_screenshot_as_png"]

    records = capture_failure_records(caplog)
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert records[0].exc_info[0] is RuntimeError


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda driver: capture_png(driver), id="capture_png"),
        pytest.param(
            lambda driver: capture_failure_embedding(driver, "Any scenario"),
            id="capture_failure_embedding",
        ),
    ],
)
def test_a_base_exception_propagates_through_both_entry_points(
    stub_driver: StubDriver,
    interrupt: type[BaseException],
    call: Callable[[Any], Any],
) -> None:
    """``KeyboardInterrupt`` and ``SystemExit`` are not suppressed.

    The module catches ``Exception``, deliberately not ``BaseException``: an
    operator's interrupt and a worker process's shutdown must still end the run.
    Both entry points are exercised, because "never raises" is a guarantee about
    ordinary failures and stops precisely here.
    """
    stub_driver.screenshot_error = interrupt

    with pytest.raises(interrupt):
        call(stub_driver)


# --------------------------------------------------------------------------- #
# encode_png -- Base64.getEncoder().encodeToString(data)
# --------------------------------------------------------------------------- #


def test_encode_png_returns_standard_base64_that_round_trips() -> None:
    """The result is an ASCII ``str`` that decodes back to the input bytes."""
    encoded = encode_png(DEFAULT_SCREENSHOT_PNG)

    assert isinstance(encoded, str)
    assert encoded == base64.b64encode(DEFAULT_SCREENSHOT_PNG).decode("ascii")
    assert encoded.isascii()
    assert base64.b64decode(encoded) == DEFAULT_SCREENSHOT_PNG


@pytest.mark.parametrize(
    "payload",
    [DEFAULT_SCREENSHOT_PNG, LONG_PAYLOAD],
    ids=["one-by-one-png", "long-payload"],
)
def test_encode_png_is_unchunked_even_where_encodebytes_would_wrap(payload: bytes) -> None:
    """No line break appears, for payloads long enough that one otherwise would.

    ``base64.encodebytes`` inserts a newline every 76 output characters -- every
    57 input bytes -- and a newline inside the ``data:<mime_type>;base64,<data>``
    URI would corrupt it in both HTML writers.  Each payload here is first proved
    long enough to be wrapped by that encoder, so the absence of a break in
    :func:`~app.reporting.screenshots.encode_png`'s output is a property of the
    function and not an accident of a short input.
    """
    assert b"\n" in base64.encodebytes(payload)

    encoded = encode_png(payload)

    assert "\n" not in encoded
    assert "\r" not in encoded
    assert base64.b64decode(encoded) == payload


@pytest.mark.parametrize(
    "payload",
    ["already a string", 42, None],
    ids=["str", "int", "none"],
)
def test_encode_png_raises_type_error_for_a_non_bytes_like_argument(payload: Any) -> None:
    """A non-bytes-like argument raises rather than being coerced or swallowed.

    Left visible on purpose: every in-module caller passes a value
    :func:`~app.reporting.screenshots.capture_png` has already validated, and
    :func:`~app.reporting.screenshots.capture_failure_embedding` suppresses the
    error in any case, so the only way to reach this is a programming error in a
    future caller -- which must not be hidden.
    """
    with pytest.raises(TypeError):
        encode_png(payload)


# --------------------------------------------------------------------------- #
# build_embedding -- scenario.attach(...) as createEmbeddingMap shapes it
# --------------------------------------------------------------------------- #


def test_build_embedding_without_a_name_has_exactly_two_keys() -> None:
    """``name=None`` omits the key rather than emitting a null.

    Mirrors the generator's ``if (name != null) embedMap.put("name", name)``, so
    the emitted JSON cannot acquire a key the source would not have written.
    The key set is asserted whole, and the camel-case spelling is asserted
    absent: ``mime_type`` with an underscore is the contract every downstream
    reader expects, including the Cucumber publisher, and is not a typo to be
    "corrected".
    """
    embedding = build_embedding(DEFAULT_SCREENSHOT_PNG, None)

    assert set(embedding) == {"mime_type", "data"}
    assert "mimeType" not in embedding
    assert "name" not in embedding


def test_build_embedding_with_a_name_has_exactly_three_keys() -> None:
    """A supplied name adds the third key and nothing else.

    AAP §0.6 fixes the embedding at bytes, a MIME type and a name -- the three
    things ``scenario.attach(screenshot, "image/png", scenario.getName())``
    supplies.
    """
    embedding = build_embedding(DEFAULT_SCREENSHOT_PNG, "UPGN-286 Login")

    assert set(embedding) == {"mime_type", "data", "name"}
    assert embedding["name"] == "UPGN-286 Login"


def test_build_embedding_writes_the_png_mime_type_from_its_own_constant() -> None:
    """``mime_type`` defaults to :data:`DEFAULT_MIME_TYPE` and admits no other
    media type.

    The parameter exists because the generator's field is per-attachment, not
    because the format is configurable: no setting reaches it, which is why the
    default is asserted against the exported constant rather than a literal.
    What the merged contract adds is that the value is *written from* that
    constant rather than carried over from the argument -- surrounding
    whitespace and a different letter case are accepted and then normalised
    away, so what reaches a ``data:`` URI is always a literal of this
    project's.
    """
    assert build_embedding(DEFAULT_SCREENSHOT_PNG, None)["mime_type"] == DEFAULT_MIME_TYPE
    assert (
        build_embedding(DEFAULT_SCREENSHOT_PNG, None, mime_type=DEFAULT_MIME_TYPE)[
            "mime_type"
        ]
        == DEFAULT_MIME_TYPE
    )
    assert (
        build_embedding(DEFAULT_SCREENSHOT_PNG, None, mime_type="  Image/PNG  ")[
            "mime_type"
        ]
        == DEFAULT_MIME_TYPE
    ), "an accepted media type must still be emitted from the module's constant"


@pytest.mark.parametrize(
    "mime_type",
    [
        "image/jpeg",
        "image/svg+xml",
        "image/png; charset=utf-8",
        "image/pngx",
        "text/html",
        "",
        None,
        42,
    ],
    ids=[
        "jpeg",
        "svg",
        "parameterised-png",
        "prefix-match",
        "html",
        "empty",
        "none",
        "int",
    ],
)
def test_build_embedding_rejects_every_media_type_but_png(mime_type: Any) -> None:
    """Anything other than exactly ``image/png`` raises rather than being
    emitted.

    A result-controlled media type is the half of the contract that a prefix
    match would surrender: ``image/svg+xml`` is an executable document in a
    browser, and a parameterised ``image/png; charset=utf-8`` is not the media
    type the writers' ``data:`` URI promises.  Raising rather than answering
    ``None`` keeps a programming error visible; the one production caller,
    :func:`~app.reporting.screenshots.capture_failure_embedding`, converts it
    into the specified no-embedding outcome, which
    :func:`test_capture_failure_embedding_suppresses_an_encoding_failure`
    covers.
    """
    with pytest.raises(ValueError, match="media type"):
        build_embedding(DEFAULT_SCREENSHOT_PNG, None, mime_type=mime_type)


@pytest.mark.parametrize(
    "png",
    [
        pytest.param(PNG_SIGNATURE, id="signature-only"),
        pytest.param(PNG_SIGNATURE + b"\x00" * 64, id="signature-and-filler"),
    ],
)
def test_build_embedding_accepts_any_png_signed_payload(png: bytes) -> None:
    """The payload test is the signature and the size bound, and stops there.

    A file that is *only* the signature is not a decodable image, and it is
    accepted: the contract is about the signature and the encoding, and no
    imaging dependency exists in this project to decode with.  A rejection here
    would mean the module had grown a test it is forbidden to perform.
    """
    embedding = build_embedding(png, "UPGN-287 CRM")

    assert embedding["data"] == base64.b64encode(png).decode("ascii")
    assert base64.b64decode(embedding["data"]) == png


def test_build_embedding_data_is_the_unchunked_base64_of_the_input() -> None:
    """``data`` is exactly what :func:`encode_png` produces for the same bytes.

    This is the base64 side of the raw-bytes-versus-base64 seam: what the port's
    own writers serialise is encoded here, exactly once.
    """
    embedding = build_embedding(DEFAULT_SCREENSHOT_PNG, "Any scenario")

    assert embedding["data"] == encode_png(DEFAULT_SCREENSHOT_PNG)
    assert base64.b64decode(embedding["data"]) == DEFAULT_SCREENSHOT_PNG
    assert "\n" not in embedding["data"]


def test_build_embedding_carries_a_hostile_name_verbatim() -> None:
    """Quotation marks, an apostrophe, a colon, an ampersand and non-ASCII survive.

    Escaping is the serialising consumer's concern -- ``json.dump`` for the
    machine artifact, Jinja's autoescaping for the two HTML outputs -- and a
    name altered here would be altered in every artifact at once, with no way to
    recover the scenario's real name from any of them.
    """
    embedding = build_embedding(DEFAULT_SCREENSHOT_PNG, HOSTILE_SCENARIO_NAME)

    assert embedding["name"] == HOSTILE_SCENARIO_NAME


# --------------------------------------------------------------------------- #
# capture_failure_embedding -- the whole of Hooks.java:14-15, composed
# --------------------------------------------------------------------------- #


def test_capture_failure_embedding_returns_the_full_embedding_on_success(
    stub_driver: StubDriver,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One capture yields the three-key embedding, with nothing logged."""
    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        embedding = capture_failure_embedding(stub_driver, "UPGN-287 CRM")

    assert embedding == {
        "mime_type": DEFAULT_MIME_TYPE,
        "data": encode_png(DEFAULT_SCREENSHOT_PNG),
        "name": "UPGN-287 CRM",
    }
    assert operation_log(stub_driver) == ["get_screenshot_as_png"]
    assert capture_failure_records(caplog) == []


def test_capture_failure_embedding_logs_a_capture_failure_exactly_once(
    stub_driver: StubDriver,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A capture failure returns ``None`` and is logged once, not twice.

    :func:`~app.reporting.screenshots.capture_png` has already logged, so a
    second record here would double every line an operator reads for a single
    failure.  The record count is the assertion, because that is the only thing
    that distinguishes the two behaviours.
    """
    stub_driver.screenshot_error = RuntimeError("session died")

    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        embedding = capture_failure_embedding(stub_driver, "UPGN-287 CRM")

    assert embedding is None
    assert len(capture_failure_records(caplog)) == 1


@pytest.mark.parametrize("target", ["build_embedding", "encode_png"])
def test_capture_failure_embedding_suppresses_an_encoding_failure(
    stub_driver: StubDriver,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    """An encoding or embedding error is logged and reported as ``None``.

    Encoding a validated ``bytes`` payload does not fail in practice, so the
    failure is injected at each of the two collaborators in turn.  The guarantee
    being asserted is absolute rather than probable: no exception raised while
    gathering optional failure evidence may reach the scenario lifecycle and
    turn a test result into an error.
    """

    def explode(*args: Any, **kwargs: Any) -> Any:
        """Raise instead of encoding.

        :param args: Ignored.
        :param kwargs: Ignored.
        :returns: Never returns.
        :raises RuntimeError: Always.
        """
        raise RuntimeError("encoder unavailable")

    monkeypatch.setattr(screenshots, target, explode)

    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        embedding = screenshots.capture_failure_embedding(stub_driver, "UPGN-287 CRM")

    assert embedding is None

    records = capture_failure_records(caplog)
    assert len(records) == 1
    assert records[0].exc_info is not None


# --------------------------------------------------------------------------- #
# The payload contract -- what is refused, and what is carried untouched
#
# Two halves of one contract, and neither means anything without the other.
#
# **Refused.**  An attachment is result-controlled data that both HTML writers
# inline into a document a human opens, so ``app/reporting/screenshots.py``
# owns the single strict test every consumer applies: the media type is exactly
# ``image/png``, the bytes begin with PNG's complete eight-byte signature, and
# the image is within ``MAX_EMBEDDING_BYTES``.  A payload failing it is
# discarded and logged -- never repaired, never re-encoded and never rendered.
# A six-byte test admits ``iVBORw0KAAAA``, which is base64 of a byte string
# that is not a PNG, so the whole signature is the test -- and
# ``SIX_BYTE_PREFIX_PAYLOAD`` is the corpus case that tells the two tests
# apart, since it differs from an image in bytes seven and eight alone.
#
# **Carried.**  Beyond the signature and the bound, nothing is judged.
# ``Hooks.java:14-15`` attaches whatever ``getScreenshotAs(OutputType.BYTES)``
# returned, the source POM declares no imaging dependency, and the module
# states the consequence -- "The image is not transformed.  No resize, crop,
# annotation or format conversion".  So a PNG-signed payload whose compressed
# image data no longer decodes, and a payload that is *only* the signature, are
# both carried through byte-exactly and unlogged: the bytes are evidence, and a
# module that repaired or re-encoded them would replace what the browser
# actually produced with its own opinion of it.
#
# Why the refusal costs nothing but the evidence: a screenshot is evidence
# about a result, never part of one.  Every refusal below leaves the scenario's
# status untouched and the driver still quit, which is asserted rather than
# assumed -- the recorders keep every attempted write.
# --------------------------------------------------------------------------- #


def test_the_payload_corpus_divides_exactly_as_the_contract_does() -> None:
    """The corpus's own premise, asserted rather than assumed.

    Every refusal test below is only decisive if its payload really is one the
    contract must reject, and every passthrough test is only decisive if its
    payload really is PNG-signed.  The division is therefore measured here,
    against this module's own spelling of the signature, and the deliberate
    exception is stated with it: the body-corrupted PNG keeps a valid
    signature, so it belongs with the carried payloads and is what catches a
    re-encoding, repairing or CRC-checking change.
    """
    assert DEFAULT_SCREENSHOT_PNG.startswith(PNG_SIGNATURE)

    signatureless = [
        TRUNCATED_PNG,
        CORRUPTED_SIGNATURE_PNG,
        SIX_BYTE_PREFIX_PAYLOAD,
        NON_IMAGE_TEXT,
        JPEG_HEADER,
        SINGLE_BYTE,
    ]
    for payload in signatureless:
        assert payload != b""
        assert not payload.startswith(PNG_SIGNATURE)
        assert not screenshots.is_png_bytes(payload)

    # The truncated header is the case a prefix test admits: it *is* the first
    # four signature bytes, so only a whole-signature test rejects it.
    assert PNG_SIGNATURE.startswith(TRUNCATED_PNG)

    # And the six-byte payload is the case an eight-byte test exists for: its
    # first six bytes are PNG's, it is longer than the signature, and only
    # bytes seven and eight tell it apart from an image.
    assert SIX_BYTE_PREFIX_PAYLOAD.startswith(PNG_SIGNATURE[:6])
    assert len(SIX_BYTE_PREFIX_PAYLOAD) > len(PNG_SIGNATURE)

    assert len(CORRUPTED_SIGNATURE_PNG) == len(DEFAULT_SCREENSHOT_PNG)
    assert CORRUPTED_SIGNATURE_PNG != DEFAULT_SCREENSHOT_PNG

    assert CORRUPTED_BODY_PNG.startswith(PNG_SIGNATURE)
    assert len(CORRUPTED_BODY_PNG) == len(DEFAULT_SCREENSHOT_PNG)
    assert CORRUPTED_BODY_PNG != DEFAULT_SCREENSHOT_PNG
    assert screenshots.is_png_bytes(CORRUPTED_BODY_PNG)

    assert len(SINGLE_BYTE) == 1


def test_the_exported_signature_is_pngs_own_eight_bytes() -> None:
    """:data:`~app.reporting.screenshots.PNG_SIGNATURE` is the specification's
    eight bytes.

    Spelled independently in this module and compared, so the constant the
    whole contract is measured against is itself measured: a high-bit byte that
    survives no 7-bit transfer, the ASCII letters ``PNG``, a CRLF pair, a DOS
    end-of-file byte and a bare LF.  Eight, not six -- the first six alone are
    also the first six bytes of a payload whose seventh and eighth are anything
    at all.
    """
    assert screenshots.PNG_SIGNATURE == PNG_SIGNATURE
    assert len(screenshots.PNG_SIGNATURE) == 8
    assert screenshots.PNG_SIGNATURE == b"\x89" + b"PNG" + b"\r\n" + b"\x1a" + b"\n"


@pytest.mark.parametrize("payload", REFUSED_PAYLOADS)
def test_capture_png_refuses_a_payload_that_is_not_png(
    stub_driver: StubDriver,
    caplog: pytest.LogCaptureFixture,
    payload: bytes,
) -> None:
    """A payload without PNG's signature is refused, logged once, and costs
    nothing else.

    The whole gate in one assertion set: ``None`` rather than the bytes, from
    exactly one capture, with exactly one ERROR record that carries
    :data:`CAPTURE_FAILURE_MESSAGE` and the payload's length -- and **not** the
    payload.  Embedding these bytes under the media type ``image/png`` is the
    one thing the contract forbids, because both HTML writers turn the value
    into a ``data:`` URI in a document a human opens.
    """
    stub_driver.screenshot_png = payload

    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        result = capture_png(stub_driver)

    assert result is None
    assert operation_log(stub_driver) == ["get_screenshot_as_png"]

    records = capture_failure_records(caplog)
    assert len(records) == 1
    assert records[0].name == SCREENSHOTS_LOGGER
    assert records[0].levelno == logging.ERROR

    message = records[0].getMessage()
    assert str(len(payload)) in message
    assert "signature" in message
    # A suppression record identifies the failure, never the picture: neither
    # the bytes nor their encoding may appear in an operator's log.
    assert base64.b64encode(payload).decode("ascii") not in message
    assert repr(payload) not in message


@pytest.mark.parametrize("payload", CARRIED_PAYLOADS)
def test_capture_png_carries_a_png_signed_payload_through_byte_exactly(
    stub_driver: StubDriver,
    caplog: pytest.LogCaptureFixture,
    payload: bytes,
) -> None:
    """A PNG-signed payload comes back unchanged and unlogged, whatever its
    pixels say.

    The no-transform half of the contract: the returned object is ``bytes``,
    equal to what the driver handed over, obtained from exactly one capture,
    and the screenshots logger said nothing at WARNING or above.  A format
    sniff, a CRC check or a dimension test would each turn the body-corrupted
    case into ``None`` plus a record; a re-encode would break the equality.
    """
    stub_driver.screenshot_png = payload

    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        result = capture_png(stub_driver)

    assert type(result) is bytes
    assert result == payload
    assert operation_log(stub_driver) == ["get_screenshot_as_png"]
    assert screenshot_warning_records(caplog) == []


@pytest.mark.parametrize("payload", REFUSED_PAYLOADS)
def test_build_embedding_refuses_a_payload_that_is_not_png(
    caplog: pytest.LogCaptureFixture,
    payload: bytes,
) -> None:
    """``build_embedding`` raises rather than encoding bytes that are not a PNG.

    ``build_embedding`` is the side of the seam the port's own writers
    serialise, so this is where an accepted foreign payload would surface as a
    ``data:image/png;base64,...`` URI carrying something that is not an image.
    It raises rather than returning ``None`` because reaching it with such a
    payload is a programming error -- the one production caller suppresses the
    error into the specified no-embedding outcome -- and it emits no record,
    since the exception is the report.
    """
    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        with pytest.raises(ValueError, match="signature"):
            build_embedding(payload, "UPGN-287 CRM")

    assert screenshot_warning_records(caplog) == []


@pytest.mark.parametrize("payload", CARRIED_PAYLOADS)
def test_build_embedding_encodes_a_png_signed_payload_without_judging_its_pixels(
    caplog: pytest.LogCaptureFixture,
    payload: bytes,
) -> None:
    """A PNG-signed payload yields the full embedding, its ``data`` exactly its
    base64.

    The key set is asserted whole and the encoding is asserted against
    :func:`base64.b64encode` directly rather than against
    :func:`~app.reporting.screenshots.encode_png`, so the two functions cannot
    drift into agreement on a transformed payload.
    """
    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        embedding = build_embedding(payload, "UPGN-287 CRM")

    assert embedding == {
        "mime_type": DEFAULT_MIME_TYPE,
        "data": base64.b64encode(payload).decode("ascii"),
        "name": "UPGN-287 CRM",
    }
    assert base64.b64decode(embedding["data"]) == payload
    assert screenshot_warning_records(caplog) == []


@pytest.mark.parametrize("payload", REFUSED_PAYLOADS)
def test_capture_failure_embedding_refuses_a_payload_that_is_not_png(
    stub_driver: StubDriver,
    caplog: pytest.LogCaptureFixture,
    payload: bytes,
) -> None:
    """Capture and attach, composed, emit no embedding for a payload that is not
    a PNG.

    The composed entry point is where the refusal has to be indistinguishable
    from a genuine capture failure, because that is the outcome the plan
    specifies for both: ``None``, one record, one capture, and no exception --
    the scenario lifecycle must not be able to tell that anything was judged.
    """
    stub_driver.screenshot_png = payload

    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        embedding = capture_failure_embedding(stub_driver, "UPGN-287 CRM")

    assert embedding is None
    assert operation_log(stub_driver) == ["get_screenshot_as_png"]
    assert len(capture_failure_records(caplog)) == 1


@pytest.mark.parametrize("payload", CARRIED_PAYLOADS)
def test_capture_failure_embedding_carries_a_png_signed_payload_end_to_end(
    stub_driver: StubDriver,
    caplog: pytest.LogCaptureFixture,
    payload: bytes,
) -> None:
    """Capture and attach, composed, neither reject nor alter a PNG-signed
    payload.

    Without this test a module that had learned to refuse a payload whose
    pixels it could not decode would look exactly like one whose session had
    died: both answer ``None``.  One capture, a three-key embedding, byte-exact
    content, and silence.
    """
    stub_driver.screenshot_png = payload

    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        embedding = capture_failure_embedding(stub_driver, "UPGN-287 CRM")

    assert embedding == {
        "mime_type": DEFAULT_MIME_TYPE,
        "data": base64.b64encode(payload).decode("ascii"),
        "name": "UPGN-287 CRM",
    }
    assert base64.b64decode(embedding["data"]) == payload
    assert operation_log(stub_driver) == ["get_screenshot_as_png"]
    assert screenshot_warning_records(caplog) == []


@pytest.mark.parametrize("payload", REFUSED_PAYLOADS)
def test_after_scenario_attaches_nothing_for_a_payload_that_is_not_png(
    hook_module: ModuleType,
    fake_context: FakeContext,
    stub_driver: StubDriver,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    """The hook loses the evidence and nothing else.

    The lifecycle form of the refusal, and the reason it is asserted here as
    well as at unit level: the whole of ``after_scenario`` -- capture, attach,
    teardown -- has to complete for a payload it refuses.  Nothing is attached,
    the failure is logged once at ERROR where an operator can grep for it, the
    driver is still quit, ``context.driver`` is still cleared, and **the
    scenario's status is untouched**: both recorders keep every attempted
    write, so that last point is an assertion rather than an assumption.

    The record's *identity segment* is asserted at unit level instead, by
    :func:`test_a_suppressed_capture_names_the_scenario_it_belongs_to`.  The
    hook does supply the identity -- ``capture_png(context.driver,
    scenario_id=_scenario_identity(scenario))`` -- but the signature refusal in
    ``app/reporting/screenshots.py`` composes its record with ``logger.error``
    directly rather than through the module's ``_suppression_record`` helper, so
    on this one path the identity reaches nothing.  What is asserted here is
    therefore the record that exists: its level, its logger and the refusal it
    reports.
    """
    monkeypatch.setattr(hook_module, "quit_driver", quit_driver_recorder(stub_driver))
    stub_driver.screenshot_png = payload
    scenario = RecordingScenario("UPGN-287 Create a new opportunity", failed=True)

    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        hook_module.after_scenario(fake_context, scenario)

    assert fake_context.attachments == []

    records = capture_failure_records(caplog)
    assert len(records) == 1
    assert records[0].name == SCREENSHOTS_LOGGER
    assert records[0].levelno == logging.ERROR
    assert "signature" in records[0].getMessage()

    assert operation_log(stub_driver) == ["get_screenshot_as_png", "quit"]
    assert stub_driver.quit_count == 1
    assert fake_context.driver is None

    assert scenario.writes == []
    assert scenario.status.writes == []
    assert scenario.status.name == "failed"


@pytest.mark.parametrize("payload", CARRIED_PAYLOADS)
def test_after_scenario_attaches_a_png_signed_payload_and_completes_the_lifecycle(
    hook_module: ModuleType,
    fake_context: FakeContext,
    stub_driver: StubDriver,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    """The hook carries a PNG-signed payload rather than validating its pixels.

    The attachment is the exact bytes under the exact MIME type, the driver is
    still quit, ``context.driver`` is still cleared, nothing is logged at
    WARNING or above, and the scenario's status is untouched.
    """
    monkeypatch.setattr(hook_module, "quit_driver", quit_driver_recorder(stub_driver))
    stub_driver.screenshot_png = payload
    scenario = RecordingScenario("UPGN-287 Create a new opportunity", failed=True)

    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        hook_module.after_scenario(fake_context, scenario)

    assert fake_context.attachments == [(DEFAULT_MIME_TYPE, payload)]

    mime_type, attached = fake_context.attachments[0]
    assert mime_type == DEFAULT_MIME_TYPE
    assert type(attached) is bytes
    assert attached == payload

    assert operation_log(stub_driver) == ["get_screenshot_as_png", "quit"]
    assert stub_driver.quit_count == 1
    assert fake_context.driver is None
    assert screenshot_warning_records(caplog) == []

    assert scenario.writes == []
    assert scenario.status.writes == []
    assert scenario.status.name == "failed"


# --------------------------------------------------------------------------- #
# The suppression record -- which scenario lost its evidence
#
# A suppressed failure leaves exactly one trace, so in a shard that ran many
# scenarios a fixed message cannot be attributed to one of them.  Both capture
# entry points therefore take a keyword-only ``scenario_id``, rendered with
# ``%r`` so that a control character in a scenario name cannot break the record
# apart, and every record in the module is composed around
# ``CAPTURE_FAILURE_MESSAGE`` rather than by rewriting it.
#
# The identity is asserted over the failures whose record the module builds
# with its ``_suppression_record`` helper -- the dead session, the driver that
# cannot be photographed, the unusable payload.  The signature and size
# refusals call ``logger.error`` with their own format string and so drop the
# identity; the refusal tests above assert the record those paths emit, and
# this section is where the identity contract itself is pinned.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda driver, identity: capture_png(driver, scenario_id=identity),
            id="capture_png",
        ),
        pytest.param(
            lambda driver, identity: capture_failure_embedding(
                driver, "UPGN-287 CRM", scenario_id=identity
            ),
            id="capture_failure_embedding",
        ),
    ],
)
def test_a_suppressed_capture_names_the_scenario_it_belongs_to(
    stub_driver: StubDriver,
    caplog: pytest.LogCaptureFixture,
    call: Callable[[Any, str | None], Any],
) -> None:
    """``scenario_id`` reaches the record, quoted, through both entry points.

    ``features/environment.py`` builds the identity from the scenario's feature
    file, line and name, and this is the only place it goes: it is not part of
    the returned value and never reaches an artifact.  ``%r`` rather than
    ``%s`` is what the newline and the quotation mark in
    :data:`SCENARIO_IDENTITY` test -- an interpolated ``%s`` would split the
    record across two lines in an operator's log.
    """
    stub_driver.screenshot_error = RuntimeError("session died")

    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        result = call(stub_driver, SCENARIO_IDENTITY)

    assert result is None

    records = capture_failure_records(caplog)
    assert len(records) == 1

    message = records[0].getMessage()
    assert CAPTURE_FAILURE_MESSAGE in message
    assert repr(SCENARIO_IDENTITY) in message
    assert "\n" not in message


def test_an_omitted_scenario_identity_leaves_the_record_as_it_was(
    stub_driver: StubDriver,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without an identity the record is :data:`CAPTURE_FAILURE_MESSAGE`,
    verbatim.

    The degradation is the contract: a caller that cannot name its scenario --
    every unit-level caller here, and any future one -- must not cause the word
    ``None`` to be logged where a scenario name belongs.
    """
    stub_driver.screenshot_error = RuntimeError("session died")

    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        assert capture_png(stub_driver) is None

    records = capture_failure_records(caplog)
    assert len(records) == 1
    assert records[0].getMessage() == CAPTURE_FAILURE_MESSAGE
    assert "None" not in records[0].getMessage()


def test_the_signature_test_is_the_only_content_test(
    screenshots_source: ast.Module,
) -> None:
    """The module's code can perform the contract's test and nothing further.

    Five facts about the syntax tree, each narrow and each necessary for a
    *further* content check to exist:

    * **One bytes literal, and it is the signature.**  A magic number has to be
      written down somewhere; this module writes exactly one, and it is PNG's
      own eight bytes bound to the exported constant.  A JPEG marker, an IEND
      chunk or any second magic number would appear here.
    * **Numeric literals only where the contract needs arithmetic** -- see
      :data:`EXPECTED_NUMERIC_LITERALS`.  No number appears anywhere on the
      image path, which rules out the index-and-compare spelling
      (``png[0] == 0x89``) and every dimension or minimum-size test.
    * **No vocabulary beyond the signature test** -- see
      :data:`BEYOND_SIGNATURE_NAMES`.  The import set is asserted whole
      elsewhere, so an imaging library cannot arrive either.
    * **The signature test is delegated, once.**  ``startswith`` appears
      against :data:`PNG_SIGNATURE` in :func:`is_png_bytes`, and every other
      caller asks that function rather than restating the test, so there is one
      definition of "is a PNG" in the project.
    * **No slice or index of a payload.**  A hand-rolled header comparison is
      spelled that way without a library, and none appears.

    A text search would be defeated by the module's own prose, which discusses
    signatures, transformation and imaging dependencies at length -- hence the
    syntax tree.
    """
    byte_literals = [
        node.value
        for node in ast.walk(screenshots_source)
        if isinstance(node, ast.Constant) and isinstance(node.value, (bytes, bytearray))
    ]
    assert byte_literals == [PNG_SIGNATURE]

    assert numeric_literals_by_owner(screenshots_source) == EXPECTED_NUMERIC_LITERALS

    used: set[str] = set()
    for node in ast.walk(screenshots_source):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)

    assert used.isdisjoint(BEYOND_SIGNATURE_NAMES)

    signature_tests = [
        ast.unparse(node)
        for node in ast.walk(screenshots_source)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "startswith"
    ]
    assert signature_tests == ["data.startswith(PNG_SIGNATURE)"], (
        "the signature test must be spelled once, against the exported constant"
    )

    payload_subscripts = [
        ast.unparse(node)
        for node in ast.walk(screenshots_source)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id in PAYLOAD_PARAMETER_NAMES
    ]
    assert payload_subscripts == []


def test_capture_png_gates_on_the_type_the_signature_and_the_declared_bound(
    screenshots_source: ast.Module,
) -> None:
    """``capture_png``'s payload tests are exactly the four documented ones.

    The types named in its ``isinstance`` calls are ``bytearray``,
    ``memoryview`` and ``bytes`` -- the normalisation of a well-behaved but
    unusual return, and the bytes-like requirement itself -- emptiness is
    ``not png``, the signature test is delegated to
    :func:`~app.reporting.screenshots.is_png_bytes`, and the only comparison in
    the function is the size bound against the exported constant.  Scoped to
    this one function because it is the capture gate: everything downstream
    receives a payload it has already accepted.  A minimum size, a dimension
    test or a hand-rolled header comparison would each add a comparison here
    and fail.
    """
    function = next(
        node
        for node in screenshots_source.body
        if isinstance(node, ast.FunctionDef) and node.name == "capture_png"
    )

    checked_types: set[str] = set()
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
        ):
            checked_types.update(
                name.id for name in ast.walk(node.args[1]) if isinstance(name, ast.Name)
            )

    assert checked_types == {"bytearray", "memoryview", "bytes"}

    comparisons = [
        ast.unparse(node)
        for node in ast.walk(function)
        if isinstance(node, ast.Compare)
    ]
    assert comparisons == ["len(png) > MAX_EMBEDDING_BYTES"]

    delegated = [
        ast.unparse(node)
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "is_png_bytes"
    ]
    assert delegated == ["is_png_bytes(png)"]


# --------------------------------------------------------------------------- #
# Module surface and boundaries
# --------------------------------------------------------------------------- #


def test_module_exports_exactly_the_documented_surface() -> None:
    """``__all__`` is the documented names, in order, each of them resolvable.

    Asserted as an ordered list rather than a set, because the declaration's
    order is what a reader of the module sees and a name appended without
    thought is exactly the drift this catches.  Two names are deliberately
    absent: the ``_TakesScreenshot`` protocol, which documents the one method a
    driver needs and has no runtime role, and ``_suppression_record``, which
    exists so the module's three suppression sites cannot word their record
    differently and is nobody else's business.
    """
    assert screenshots.__all__ == EXPECTED_EXPORTS

    for name in EXPECTED_EXPORTS:
        assert hasattr(screenshots, name)

    private = [
        name
        for name in ("_TakesScreenshot", "_suppression_record", "_IDENTITY_SEGMENT")
        if name in screenshots.__all__
    ]
    assert private == [], f"{private} are module-private and must not be exported"


def test_default_mime_type_is_the_java_literal() -> None:
    """``DEFAULT_MIME_TYPE`` is ``"image/png"``, verbatim from ``Hooks.java:15``.

    Not configurable, and not a format switch: the same reasoning that forbids
    an enable flag forbids a format setting, and the six configuration keys
    contain neither.
    """
    assert DEFAULT_MIME_TYPE == "image/png"


def test_module_imports_only_the_standard_library(screenshots_source: ast.Module) -> None:
    """Neither ``app.config`` nor ``app.automation`` -- nor selenium -- is imported.

    Three AAP constraints meet in this one assertion.  No configuration import,
    because there is no enable flag to read and the configuration surface stays
    at its six keys.  No ``app.automation`` import, because the driver arrives as
    a parameter -- which is what keeps the capture-before-quit ordering the
    caller's to enforce.  No Selenium import, because only ``app/automation``
    may make one.  The import set is asserted whole, so a new dependency of any
    kind has to be justified against this test.
    """
    imported: set[str] = set()

    for node in ast.walk(screenshots_source):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # A relative import would carry no module name and could only reach
            # inside app/reporting, which this module also does not do.
            assert node.level == 0
            assert node.module is not None
            imported.add(node.module)

    assert imported == EXPECTED_IMPORTS
    assert not any(name.startswith("app.") for name in imported)
    assert not any(name.split(".")[0] == "selenium" for name in imported)


def test_module_never_reaches_the_filesystem(screenshots_source: ast.Module) -> None:
    """No name in the module's code can open, create or write a file.

    AAP §0.6 makes the embedding inline base64: no path module defines a
    screenshot location, and a screenshot written beside the report would break
    the self-contained guarantee of ``target/cucumber-reports.html``.  The
    syntax tree is searched rather than the text because the module's own prose
    discusses files at length, and a text search would report the discussion.
    """
    used: set[str] = set()

    for node in ast.walk(screenshots_source):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)

    assert used.isdisjoint(FILESYSTEM_NAMES)


# --------------------------------------------------------------------------- #
# features/environment.py -- after_scenario, at integration level
#
# The real capture helper runs in every test below; only the session teardown is
# substituted, and only because a unit test's driver is never in the worker's
# slot for the real ``quit_driver`` to find.
# --------------------------------------------------------------------------- #


def test_after_scenario_captures_once_before_quitting_a_failed_scenario(
    hook_module: ModuleType,
    fake_context: FakeContext,
    stub_driver: StubDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture happens exactly once, and strictly before the driver is quit.

    The single most important property of the function: once the session is
    quit there is nothing left to photograph, so a reordering here would empty
    every failure report in the suite while leaving every test that counts calls
    still passing.  The whole ordered log is asserted, which is what makes the
    order -- not merely the occurrence -- the subject.
    """
    monkeypatch.setattr(hook_module, "quit_driver", quit_driver_recorder(stub_driver))
    scenario = RecordingScenario("UPGN-286 Login with invalid credentials", failed=True)

    hook_module.after_scenario(fake_context, scenario)

    assert operation_log(stub_driver) == ["get_screenshot_as_png", "quit"]
    assert stub_driver.quit_count == 1
    assert scenario.status.has_failed_calls == 1
    assert fake_context.driver is None


def test_after_scenario_attaches_raw_bytes_under_the_png_mime_type(
    hook_module: ModuleType,
    fake_context: FakeContext,
    stub_driver: StubDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One attachment is recorded, as ``(DEFAULT_MIME_TYPE, <raw PNG bytes>)``.

    Raw bytes, **not** the base64 string: behave's ``Context.attach`` documents a
    bytes-like payload and behave's own JSON formatter base64-encodes whatever it
    is handed, so a pre-encoded string would be encoded twice and render as
    nothing at all.  Both halves are asserted -- that the payload is the PNG
    bytes, and that it is not their encoding.
    """
    monkeypatch.setattr(hook_module, "quit_driver", quit_driver_recorder(stub_driver))
    scenario = RecordingScenario("UPGN-286 Login with invalid credentials", failed=True)

    hook_module.after_scenario(fake_context, scenario)

    assert fake_context.attachments == [(DEFAULT_MIME_TYPE, DEFAULT_SCREENSHOT_PNG)]

    mime_type, payload = fake_context.attachments[0]
    assert mime_type == "image/png"
    assert isinstance(payload, bytes)
    assert payload != encode_png(DEFAULT_SCREENSHOT_PNG)


def test_after_scenario_captures_nothing_for_a_passing_scenario(
    hook_module: ModuleType,
    fake_context: FakeContext,
    stub_driver: StubDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A passing scenario is not photographed, but is still torn down.

    ``Driver.closeDriver()`` sits outside the ``if`` at ``Hooks.java:17``, so
    teardown is unconditional while capture is not.  There is no else-branch in
    the source, no passing-scenario path in the port, and no enable flag that
    could create one.
    """
    monkeypatch.setattr(hook_module, "quit_driver", quit_driver_recorder(stub_driver))
    scenario = RecordingScenario("UPGN-285 Login with valid credentials", failed=False)

    hook_module.after_scenario(fake_context, scenario)

    assert operation_log(stub_driver) == ["quit"]
    assert fake_context.attachments == []
    assert stub_driver.quit_count == 1
    assert fake_context.driver is None


def test_after_scenario_suppresses_a_capture_failure_without_touching_the_status(
    hook_module: ModuleType,
    fake_context: FakeContext,
    stub_driver: StubDriver,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead session costs the evidence, nothing else.

    AAP deviation 19 in its integration form: the error is logged where an
    operator can grep for it, no embedding is emitted, the driver is still quit,
    ``context.driver`` is still cleared, and **the scenario's status is
    unchanged**.  The status and the scenario both record every attempted write,
    so the last of those is an assertion rather than an assumption.
    """
    monkeypatch.setattr(hook_module, "quit_driver", quit_driver_recorder(stub_driver))
    stub_driver.screenshot_error = RuntimeError("session died")
    scenario = RecordingScenario("UPGN-288 Login validation message", failed=True)

    with caplog.at_level(logging.DEBUG, logger=SCREENSHOTS_LOGGER):
        hook_module.after_scenario(fake_context, scenario)

    assert fake_context.attachments == []
    assert operation_log(stub_driver) == ["get_screenshot_as_png", "quit"]
    assert stub_driver.quit_count == 1
    assert fake_context.driver is None

    records = capture_failure_records(caplog)
    assert len(records) == 1
    assert records[0].name == SCREENSHOTS_LOGGER

    assert scenario.writes == []
    assert scenario.status.writes == []
    assert scenario.status.name == "failed"
    assert scenario.status.has_failed() is True


def test_after_scenario_quits_the_driver_even_when_attaching_raises(
    hook_module: ModuleType,
    fake_context: FakeContext,
    stub_driver: StubDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Teardown runs from a ``finally``, so evidence gathering cannot leak a browser.

    Driven by making the attach step itself fail, which is the last statement
    that could plausibly raise inside the ``try``.  Getting this wrong leaks one
    browser process per failing scenario and, across a process pool, exhausts
    the host -- so the error is asserted to propagate *and* the teardown to have
    happened, since swallowing it would hide the defect that caused it.
    """
    monkeypatch.setattr(hook_module, "quit_driver", quit_driver_recorder(stub_driver))
    fake_context.attach = raising_attach(AttachFailure("formatter rejected the embedding"))
    scenario = RecordingScenario("UPGN-286 Login with invalid credentials", failed=True)

    with pytest.raises(AttachFailure):
        hook_module.after_scenario(fake_context, scenario)

    assert operation_log(stub_driver) == ["get_screenshot_as_png", "quit"]
    assert stub_driver.quit_count == 1
    assert fake_context.driver is None
