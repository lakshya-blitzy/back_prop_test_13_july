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
invokes the teardown; the reference ``target/cucumber.json`` corroborates it by
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
* **No enable flag.**  ``README.md:42-43`` claims screenshots "for your tests
  if you enable it", but no setting and no branch in ``Hooks`` provides one, so
  under the plan's precedence rule that claim is aspirational.  The
  configuration surface stays at its six keys -- ``browser``,
  ``web.table.url``, ``url``, ``username``, ``password``, ``EmplTitle`` -- none
  of which concerns screenshots, and ``app/config.py`` is not imported.
* **Nothing is written to disk.**  The embedding is inline base64; no path
  module defines a screenshot location, and writing one would break the
  self-contained guarantee of ``target/cucumber-reports.html``.
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
"""

from __future__ import annotations

import base64
import logging
from typing import Final, Protocol

__all__ = [
    "CAPTURE_FAILURE_MESSAGE",
    "DEFAULT_MIME_TYPE",
    "build_embedding",
    "capture_failure_embedding",
    "capture_png",
    "encode_png",
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
#: rather than against a duplicated string literal.
CAPTURE_FAILURE_MESSAGE: Final[str] = (
    "Failure screenshot could not be captured; no embedding will be emitted "
    "and the scenario status is unchanged"
)


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


def capture_png(driver: _TakesScreenshot) -> bytes | None:
    """Capture the current browser viewport as PNG bytes.

    This is the port of ``((TakesScreenshot) Driver.getDriver())``
    ``.getScreenshotAs(OutputType.BYTES)`` at ``Hooks.java:14``.  The driver
    arrives as a parameter rather than being looked up, so the caller -- which
    owns the scenario lifecycle -- can guarantee the session is still live.

    Args:
        driver: Any object exposing ``get_screenshot_as_png()``.  In production
            this is the live WebDriver session for the failed scenario, taken
            from the driver holder *before* it is quit.

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
        logger.exception(CAPTURE_FAILURE_MESSAGE)
        return None

    # Defensive normalisation.  A well-behaved driver returns non-empty
    # ``bytes``; anything else would end up as a truncated or empty ``data:``
    # URI in both HTML artifacts, which renders as a broken image and is worse
    # than no embedding at all.  Such a payload is therefore treated exactly as
    # a capture failure: logged, and reported as ``None``.
    if isinstance(png, (bytearray, memoryview)):
        png = bytes(png)
    if not isinstance(png, bytes) or not png:
        logger.error(
            "%s (driver returned an unusable screenshot payload: %s)",
            CAPTURE_FAILURE_MESSAGE,
            type(png).__name__,
        )
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
            reaches this parameter.

    Returns:
        A mapping with the keys ``mime_type`` and ``data``, plus ``name`` when
        a name was supplied.  The name is carried verbatim -- quotation marks,
        apostrophes and non-ASCII characters in scenario names are preserved,
        and JSON or HTML escaping is the serialising consumer's concern, not
        this function's.

    Raises:
        TypeError: Propagated from :func:`encode_png` if ``png`` is not
            bytes-like.
    """
    embedding: dict[str, str] = {
        "mime_type": mime_type,
        "data": encode_png(png),
    }
    if name is not None:
        embedding["name"] = name
    return embedding


def capture_failure_embedding(
    driver: _TakesScreenshot,
    scenario_name: str | None,
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
            ``None`` omits the name, per :func:`build_embedding`.

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
    png = capture_png(driver)
    if png is None:
        # Already logged by ``capture_png``; log once per failure, not twice.
        return None

    try:
        return build_embedding(png, scenario_name)
    except Exception:
        # Encoding a validated ``bytes`` payload does not fail in practice, but
        # the guarantee this function offers its caller is absolute: no
        # exception raised while gathering optional failure evidence may reach
        # the scenario lifecycle and turn a test result into an error.
        logger.exception(CAPTURE_FAILURE_MESSAGE)
        return None
