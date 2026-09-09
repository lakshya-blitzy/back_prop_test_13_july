"""Shared fixtures for the unit suite of the Testinium-QA Python port.

This module is the foundation of ``tests/``: every other test module in the
suite reaches its collaborators through the fixtures defined here, and it
contains **no test functions of its own**.  A failure here is a failure of the
whole suite, which is why the surface below is explicit, side-effect-free and
built entirely out of the seams the production modules already publish.

Why this suite exists
---------------------
Neither this repository at its base revision nor the authoritative Java
reference contains any test code - the reference ``src/`` holds only ``main``.
This suite therefore preserves no source behaviour; it exists under the
specification's *"Standards for code this port writes"* exception (AAP 0.1.3),
which applies the coverage targets to ``tests/**`` **and only there**, and it is
recorded as AAP deviation 13.  Discovery is owned by the repository-root
``pytest.ini`` (``testpaths = tests``), which carries test selection only; the
four per-package coverage gates - ``app/utils`` 90, ``app/pages`` 85,
``app/automation`` 80, ``app/reporting`` 80 - live in the ``Makefile``'s
``coverage`` target, which runs pytest once per scope and stops at the first
miss.  Nothing in this file measures or gates coverage.

What the suite asserts: structure, never bytes
----------------------------------------------
Byte-stability across runs is impossible and no test may demand it (AAP 0.6).
``target/cucumber.json`` carries a per-scenario ``start_timestamp`` and measured
nanosecond durations; failure text embeds a Python traceback; both HTML outputs
surface timing; and screenshot bytes differ from one capture to the next.  What
*is* deterministic, and therefore what the suite pins, is **structure**:

* features in source order, and scenarios in line order within a feature;
* the Background repeated in the same position among a feature's elements;
* rerun-manifest entries grouped one line per feature, with line numbers
  ascending;
* an identical PrettyReports page set for identical inputs.

:func:`normalize_volatile` is the shared helper that makes those comparisons
possible: it canonicalises exactly the volatile fields and leaves every
structural one alone, so a diff that survives it is a real difference.  No test
compares a whole generated artifact byte for byte.

A note for anyone adding an ordering assertion: ``sortingMethod:
'ALPHABETICAL'`` in ``Jenkins`` is a **publisher display option**.  It imposes
nothing whatsoever on the artifacts this port writes, so no writer sorts on its
account and no test may expect it to.

Standing constraints on every fixture here
------------------------------------------
The two user-specified rules for this project carry no actionable directive -
neither names a file, a standard, a pattern or a prohibition - so neither
governs this file.  Their silence is not licence to lower the bar; ordinary
enterprise practice for this stack governs instead, and for this module it
means:

* **No network access, and no browser.**  Nothing here launches a driver.  The
  WebDriver stand-in is :class:`StubDriver`, a duck-typed recorder.
* **No selenium import.**  Only ``app/automation/{driver,waits,interactions}.py``
  may import selenium in this port, and ``app/reporting/screenshots.py`` is
  deliberately selenium-free because it takes its driver duck-typed.  Importing
  selenium here would quietly make that invariant untestable.
* **No dependency on a live Odoo instance and none on a populated
  ``configuration.properties``.**  A missing properties file is tolerated by
  design (``ConfigurationReader:21-24``), and every fixture below works without
  one.
* **No leaked global state.**  The one autouse fixture returns the two
  process-global holders - the properties cache and the worker-local driver slot
  - to their pre-test state around every test.
* **No writing into the repository's real ``target/``.**  Path-taking code is
  driven through the ``base=`` seam with :fixture:`tmp_artifact_root`.

Import path ownership
---------------------
``pytest.ini`` states plainly that making ``import app`` and
``import features.steps.<module>`` resolve from the repository root is this
file's job, and that splitting it across two files would leave neither of them
the answer.  The bootstrap immediately below is that job; see its comment for
why it cannot be dropped as redundant.

There is deliberately **no** ``tests/__init__.py``.  The AAP 0.3.1 target tree
does not name one and its absence is load-bearing: with no package marker,
pytest's *prepend* import mode puts ``tests/`` itself on ``sys.path``, so a
sibling module such as ``test_paths`` remains importable as a top-level module.
"""

from __future__ import annotations

import base64
import contextlib
import json
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, MutableMapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NamedTuple

import pytest

if TYPE_CHECKING:  # pragma: no cover - read by type checkers, never at runtime
    # ``TYPE_CHECKING`` is ``False`` when the interpreter runs, so this block
    # never executes and importing this module therefore never imports Flask.
    # That is the same device ``app/__init__.py`` uses, and for the same
    # reason: the annotations below stay informative without putting a Flask
    # import at collection time.  ``from __future__ import annotations`` above
    # is what keeps the annotations themselves un-evaluated.
    from flask import Flask
    from flask.testing import FlaskClient

# --------------------------------------------------------------------------- #
# The import-path bootstrap.  This runs at module import time, before the first
# ``app`` import below, and it is the whole of what ``pytest.ini`` delegates
# here.
#
# Why it is required, so that a later reader does not remove it as redundant:
# pytest's default *prepend* import mode inserts the test module's **basedir**
# onto ``sys.path`` - that is ``tests/``, because there is no
# ``tests/__init__.py`` - and never the rootdir.  A plain ``pytest`` invocation
# (as opposed to ``python -m pytest``, which prepends the working directory)
# adds nothing else.  So without this insertion ``import app`` fails whenever
# the project is not pip-installed, which is the normal case for ``make unit``
# and is the measured state of a fresh clone here.
#
# Insertion is at position 0 and conditional: an entry already present is left
# exactly where it is, so a deliberate ordering - an editable install, or a
# PYTHONPATH set by a runner script - is never reshuffled by this file.
# --------------------------------------------------------------------------- #

#: The repository root, derived from this file's own position: ``tests/`` ->
#: repository root.  ``resolve()`` first, so the value is absolute and free of
#: symlinks and therefore comparable against paths the production modules build.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Imported after the bootstrap above, which is exactly why these two are not at
# the top of the file (E402 is suppressed for that reason and no other).
#
# Both are standard-library-only modules by contract, so importing them at
# collection time cannot fail for a missing third-party package and cannot
# start anything.  ``app/utils/properties.py`` documents its
# standard-library-only invariant; ``app/utils/paths.py`` imports nothing from
# the ``app`` package, which is what lets it be used in a worker process that
# never builds a Flask application.
#
# Deliberately absent from this module's top level: Flask, selenium and behave.
# ``app/__init__.py`` defers every Flask import into ``create_app()`` so that
# ``import app.utils.paths`` stays cheap in a worker; importing Flask here would
# defeat that.  selenium is barred outright (see the module docstring), and
# behave is imported inside :func:`load_step_registry` so that the engine is
# loaded only by the tests that actually resolve a step phrase.
from app.utils import paths, properties  # noqa: E402

# --------------------------------------------------------------------------- #
# Fixture-data locations
#
# The three files are written by the ``tests/fixtures/`` agent and are read
# **by path** here - never reproduced as literal content in this file, which
# would fork the baseline the moment either copy was edited.
# --------------------------------------------------------------------------- #

#: Directory holding the golden baselines and the hand-built result set.
FIXTURES_DIR: Final[Path] = Path(__file__).resolve().parent / "fixtures"

#: The ``HEAD`` side of the committed ``target/cucumber.json``, taken alone.
#: The committed artifact carries one unresolved merge-conflict block whose two
#: sides do not concatenate into valid JSON, so this baseline is one side of it
#: rather than a marker-stripped whole (AAP 0.6).
GOLDEN_CUCUMBER_PATH: Final[Path] = FIXTURES_DIR / "golden_cucumber.json"

#: The committed ``target/rerun.txt``, which is marker-free and needed no such
#: surgery: one line, one feature, its two failing line numbers appended.
GOLDEN_RERUN_PATH: Final[Path] = FIXTURES_DIR / "golden_rerun.txt"

#: A hand-built merged result set in the port's **internal** schema - the shape
#: ``app/reporting/events.py`` owns - and the shared input to every writer test.
SAMPLE_RESULTS_PATH: Final[Path] = FIXTURES_DIR / "sample_results.json"

#: Encoding for every fixture read here.  The golden artifacts and the sample
#: result set are UTF-8 JSON and UTF-8 text respectively; this is deliberately
#: *not* ``properties.DEFAULT_ENCODING``, which is the ISO-8859-1 of
#: ``java.util.Properties`` and applies to ``configuration.properties`` alone.
FIXTURE_ENCODING: Final[str] = "utf-8"

# --------------------------------------------------------------------------- #
# Canonical stand-ins used by :func:`normalize_volatile`
#
# Each placeholder replaces a value that legitimately varies between runs while
# preserving the one structural fact about it that does not: that the key was
# present at all.  A step that omits ``duration`` because it was skipped stays
# distinguishable from one that reports a duration of zero, which is precisely
# the distinction the JVM schema draws (AAP 0.6).
# --------------------------------------------------------------------------- #

#: Replaces ``start_timestamp``, ``started_at`` and ``generated_at``.
TIMESTAMP_PLACEHOLDER: Final[str] = "<timestamp>"

#: Replaces a measured ``duration``.  An ``int``, because the JVM schema's
#: durations are nanosecond integers and a normalized document should still type
#: check the way the real one does.
DURATION_PLACEHOLDER: Final[int] = 0

#: Replaces ``error_message``, whose content is a Python assertion message plus
#: a traceback and therefore varies with the interpreter and the run
#: (AAP deviation 16).
ERROR_MESSAGE_PLACEHOLDER: Final[str] = "<error-message>"

#: Replaces the base64 payload of an embedding.  Screenshot bytes differ from
#: capture to capture even for an identical page.
EMBEDDED_DATA_PLACEHOLDER: Final[str] = "<embedded-data>"

#: The keys whose values are replaced with :data:`TIMESTAMP_PLACEHOLDER`.
TIMESTAMP_KEYS: Final[frozenset[str]] = frozenset(
    {"start_timestamp", "started_at", "generated_at"}
)

#: A deterministic, non-empty, genuinely well-formed 1x1 PNG.  Returned by
#: :meth:`StubDriver.get_screenshot_as_png` by default so that a test exercising
#: ``app/reporting/screenshots.py`` gets bytes that survive its defensive
#: emptiness check and that a base64 round-trip can be asserted against without
#: pinning a platform-dependent value.
DEFAULT_SCREENSHOT_PNG: Final[bytes] = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/"
    "q842iQAAAABJRU5ErkJggg=="
)

#: Default tag name reported by a :class:`StubElement` that was not programmed
#: with one.  Neutral on purpose: a test that cares about the tag sets it.
DEFAULT_TAG_NAME: Final[str] = "div"

#: The behave step-registry bucket every step definition in this port lands in.
#: AAP deviation 7 requires ``@step`` and nothing else, so that resolution
#: cannot depend on the invoking keyword the way ``@given``/``@when``/``@then``
#: would; measured against the tree, all 91 definitions register here.
STEP_BUCKET: Final[str] = "step"

#: Every bucket behave keeps, in the order :func:`find_all_step_matches` scans
#: them.  Only :data:`STEP_BUCKET` is expected to be populated; the other three
#: are scanned anyway so that an accidental keyword-bound registration is found
#: and reported rather than silently missed.
STEP_BUCKETS: Final[tuple[str, ...]] = ("step", "given", "when", "then")


# =========================================================================== #
# Repository layout
# =========================================================================== #


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The repository root as an absolute, symlink-free :class:`~pathlib.Path`.

    The same value the import bootstrap at the top of this module put on
    ``sys.path``, published as a fixture so that a test needing to reach a
    tracked file - a feature file, ``behave.ini``, ``pytest.ini`` - locates it
    without recomputing the root and without depending on the working directory
    pytest happened to be launched from.

    Session-scoped because it is an immutable value derived from this file's
    position, so there is nothing for one test to hand to the next.

    :returns: The repository root directory.
    """
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """The ``tests/fixtures/`` directory holding the suite's baseline data.

    Prefer the three loader fixtures - :fixture:`golden_cucumber`,
    :fixture:`golden_rerun` and :fixture:`sample_result_set` - over reading a
    file through this path.  It is published for the cases they do not cover,
    such as a test that asserts a baseline's own on-disk properties (the
    50-byte length of the rerun manifest, say) rather than its parsed content.

    :returns: The fixture-data directory.
    """
    return FIXTURES_DIR


# =========================================================================== #
# The Flask application
#
# One rule governs this section, and it is an AAP 0.4.2 invariant: ``wsgi.py``,
# ``run.py`` and ``tests/conftest.py`` all obtain their application from
# ``create_app()``.  Nothing here constructs ``Flask(...)`` directly, and
# nothing here imports the ``web`` blueprint, the error handlers or the
# ``run-tests`` command in order to register them - ``app/__init__.py`` is the
# sole registration point in this port, so a fixture that wired any of them up
# itself would be testing an application no user of this project can obtain.
# =========================================================================== #

#: Flask config applied to every application this suite builds.  ``TESTING``
#: propagates exceptions out of the test client instead of converting them to a
#: 500 response, which is what lets a route test see the real traceback.
BASE_FLASK_CONFIG: Final[Mapping[str, object]] = {"TESTING": True}


@pytest.fixture
def flask_app(request: pytest.FixtureRequest) -> Flask:
    """A freshly built read-only artifact-viewer application.

    Built by calling ``app.create_app()`` and by no other means.  The factory
    documents itself as free of import-time side effects and safe to call any
    number of times in one interpreter - it holds no module-level state - so a
    per-test application cannot be influenced by an earlier one, which is the
    isolation this suite depends on.

    ``{"TESTING": True}`` is delivered through the factory's own
    ``config_overrides`` parameter rather than assigned to ``app.config``
    afterwards.  The difference matters: the factory applies overrides *before*
    the blueprint, the error handlers and the command are registered, so a value
    under test is in place for every registration that follows, and routing the
    value through the parameter also exercises the documented signature instead
    of bypassing it.

    A test needing further Flask config parametrizes this fixture indirectly,
    and its mapping is merged over :data:`BASE_FLASK_CONFIG`::

        @pytest.mark.parametrize(
            "flask_app", [{"SERVER_NAME": "localhost"}], indirect=True
        )
        def test_something(flask_app): ...

    Note what is *not* configurable here: the port's six
    ``configuration.properties`` keys never travel through Flask config.  They
    are reached through ``app/config.py``, whose precedence is behave userdata
    first and the properties file second (AAP 0.4.1).

    :param request: pytest's request object, read only for an optional
        ``param`` carrying extra Flask config.
    :returns: A ``Flask`` application carrying the single ``web`` blueprint, the
        two error handlers and the ``run-tests`` command.
    :raises TypeError: Propagated from the factory if an indirect parameter is
        supplied that is not a mapping - rejected there before anything is
        constructed.
    """
    # Imported here, not at module level.  Binding the name costs nothing -
    # ``app/__init__.py``'s only module-level import is a typing one - but
    # keeping it local documents that Flask enters this process only when a test
    # actually asks for an application.
    from app import create_app

    overrides: dict[str, object] = dict(BASE_FLASK_CONFIG)

    extra = getattr(request, "param", None)
    if extra is not None:
        if not isinstance(extra, Mapping):
            # Neither coerced nor ignored.  Handing the value straight to the
            # factory lets *it* raise the documented TypeError, before anything
            # is constructed, so a mistyped parametrization fails at its own
            # cause rather than producing a half-configured application.
            return create_app(extra)

        overrides.update(extra)

    return create_app(overrides)


@pytest.fixture
def client(flask_app: Flask) -> Iterator[FlaskClient]:
    """A test client for :fixture:`flask_app`, with its context torn down.

    Entered as a context manager, which is what keeps the request and
    application contexts of the last request alive for the duration of the test
    - so an assertion may inspect them after a call - and what guarantees they
    are popped afterwards even when the test fails.  A client left un-entered
    would leak a context onto the next test in the same process.

    Every route in this port is read-only: no request this client can make
    writes to disk and none starts a test run (AAP 0.3.1).

    :param flask_app: The application under test.
    :yields: A ``FlaskClient`` bound to that application.
    """
    with flask_app.test_client() as test_client:
        yield test_client


# =========================================================================== #
# The WebDriver stand-in
#
# The most reused fixture in the suite: the ten ``test_steps_<area>.py`` parity
# modules, ``test_base_page.py``, ``test_pages.py``, ``test_screenshots.py`` and
# ``test_interactions.py`` all drive their subject through it.  Defined once,
# here, so that "the same observable operations in the same order" means the
# same thing in every one of them.
#
# It is a hand-written recorder rather than a ``unittest.mock.Mock`` on purpose,
# for two reasons that both bite in this suite specifically:
#
#   * a Mock answers *any* attribute, so ``driver.titel`` would yield a fresh
#     Mock and a parity assertion would pass against a typo, whereas the classes
#     below carry ``__slots__`` and raise ``AttributeError`` for an unknown read
#     *and* an unknown write;
#   * ``mock_calls`` records per-mock, so an element's ``click`` and the
#     driver's ``find_element`` land in different sequences and their relative
#     order - which is exactly what parity is about - is lost.  Here every
#     element operation appends to the *driver's* single ordered log.
#
# It imports no selenium, which is what keeps two invariants checkable rather
# than merely asserted: that ``app/reporting/screenshots.py`` really does take
# its driver duck-typed, and that only ``app/automation`` needs the browser
# binding at all.
# =========================================================================== #

#: A ``(strategy, value)`` pair - the shape ``app/pages/base_page.py`` builds
#: from its ``By`` constants and unpacks straight into ``find_element(*locator)``.
Locator = tuple[str, str]

#: One entry of the ordered call log: an operation name and its arguments.
Call = tuple[str, tuple[Any, ...]]

#: Prefix distinguishing an operation performed on an element from one performed
#: on the driver.  An element entry's first argument is always the locator that
#: produced it, so ``("element.click", (("id", "login"),))`` reads as "clicked
#: the element found by ``(By.ID, "login")``".
ELEMENT_PREFIX: Final[str] = "element."

#: Sentinel for "nothing was programmed", so that a programmed ``None`` - the
#: value Selenium returns for an absent attribute - stays distinguishable from
#: an unprogrammed lookup.
_UNSET: Final[Any] = object()


def _raise(error: BaseException | type[BaseException]) -> None:
    """Raise a programmed failure, accepting either an instance or a class.

    Lets a test write ``driver.screenshot_error = RuntimeError`` or
    ``driver.screenshot_error = RuntimeError("session died")`` and get the same
    behaviour from either, so no test has to remember which form the stub wants.

    :param error: The exception to raise, as an instance or as a class that is
        instantiated with no arguments.
    :raises BaseException: Always - that is the entire purpose.
    """
    raise error() if isinstance(error, type) else error


class StubElement:
    """A recorder standing in for one ``WebElement``.

    Holds no state of its own beyond its identity: every value it reports and
    every operation it records is resolved against the :class:`StubDriver` that
    created it.  That indirection is deliberate - a test may programme a value
    *after* the element has been handed out, which is the normal shape of a
    parity test that arranges the page's answers once at the top and then walks
    a step body - and it is what keeps a single ordered log for the whole
    interaction.

    Instances are not created directly; :meth:`StubDriver.find_element` and
    :meth:`StubDriver.find_elements` create and cache them.
    """

    __slots__ = ("_element_id", "_owner", "index", "locator")

    def __init__(self, owner: StubDriver, locator: Locator, index: int, element_id: str) -> None:
        """Bind this element to the driver that found it.

        :param owner: The driver whose log and programmed values this element
            reads and writes.
        :param locator: The ``(strategy, value)`` pair that produced it, carried
            into every log entry it appends.
        :param index: Position among the elements that locator yields - always
            ``0`` for a ``find_element`` result, and the list position for a
            ``find_elements`` one, so a test may programme one member of a list
            differently from its siblings.
        :param element_id: A stable synthetic identifier, exposed as
            :attr:`id`.
        """
        self._owner = owner
        self.locator = locator
        self.index = index
        self._element_id = element_id

    # -- identity ---------------------------------------------------------- #

    @property
    def id(self) -> str:
        """A stable synthetic element id.

        Selenium's ``WebElement`` carries the id the remote end assigned, and
        some collaborators read it without ever dereferencing the element.
        Exposing a deterministic one keeps those paths working; it is not logged,
        because reading an identifier is not an operation performed on the page.

        :returns: An identifier unique within the owning driver and stable for
            the life of this element.
        """
        return self._element_id

    def __repr__(self) -> str:
        """Render the locator and index, which is what a failure needs to see.

        :returns: A representation naming the locator and, when it is one of
            several, the index.
        """
        return f"<StubElement {self.locator!r} index={self.index}>"

    # -- operations, each one logged --------------------------------------- #

    def click(self) -> None:
        """Record a click.

        :returns: ``None``, matching ``WebElement.click``.
        """
        self._owner._record_element(self, "click")

    def send_keys(self, *value: Any) -> None:
        """Record typing, preserving the arguments exactly as they arrived.

        ``app/automation/interactions.py`` calls ``element.send_keys(*sequence)``
        with the literal text first and each resolved ``Keys`` member after it,
        so the arguments are logged unflattened and in order - the sequence is
        the behaviour under test and joining it would erase it.

        :param value: The strings and key literals to type.
        :returns: ``None``.
        """
        self._owner._record_element(self, "send_keys", *value)

    def clear(self) -> None:
        """Record a field clear.

        :returns: ``None``.
        """
        self._owner._record_element(self, "clear")

    def get_attribute(self, name: str) -> Any:
        """Return the programmed value of one attribute, recording the read.

        The seam ``test_steps_login.py`` needs: the ``@UPGN-288`` outline reads
        the browser's own ``validationMessage`` from the login input
        (``LoginSD.java:56``) and the ``@UPGN-289`` outline reads ``type`` to
        prove the password is masked.  Both are programmed with
        :meth:`StubDriver.set_attribute`.

        :param name: The attribute to read.
        :returns: The programmed value, or ``None`` when nothing was
            programmed - which is what Selenium returns for an absent
            attribute, so an un-programmed read behaves like a missing
            attribute rather than an error.
        """
        self._owner._record_element(self, "get_attribute", name)
        return self._owner._programmed_value("attributes", self.locator, self.index, name, None)

    def is_displayed(self) -> bool:
        """Return the programmed visibility, recording the read.

        :returns: The programmed value, defaulting to ``True`` - an element the
            driver just found is visible unless a test says otherwise.
        """
        self._owner._record_element(self, "is_displayed")
        return bool(
            self._owner._programmed_value("displayed", self.locator, self.index, None, True)
        )

    def is_selected(self) -> bool:
        """Return the programmed selection state, recording the read.

        :returns: The programmed value, defaulting to ``False``.
        """
        self._owner._record_element(self, "is_selected")
        return bool(
            self._owner._programmed_value("selected", self.locator, self.index, None, False)
        )

    @property
    def text(self) -> str:
        """Return the programmed visible text, recording the read.

        A property rather than a method, matching Selenium, so a step body that
        reads ``element.text`` needs no adaptation - and the read is logged,
        because a step reading the page is an observable operation whose
        position in the sequence a parity test asserts.

        :returns: The programmed text, or ``""`` when nothing was programmed.
        """
        self._owner._record_element(self, "text")
        return str(self._owner._programmed_value("texts", self.locator, self.index, None, ""))

    @property
    def tag_name(self) -> str:
        """Return the programmed tag name, recording the read.

        :returns: The programmed tag name, or :data:`DEFAULT_TAG_NAME`.
        """
        self._owner._record_element(self, "tag_name")
        return str(
            self._owner._programmed_value(
                "tag_names", self.locator, self.index, None, DEFAULT_TAG_NAME
            )
        )


class StubDriver:
    """A recorder standing in for a Selenium ``WebDriver``.

    Two things make it useful, and they are the two things the parity obligation
    needs:

    **One ordered log.**  :attr:`calls` is a list of ``(operation, args)``
    tuples in the order the operations happened, and element operations append
    to it too - tagged with the locator that produced the element - so a test
    can assert that a step navigated, then found a field, then typed into it,
    rather than merely that all three occurred.

    **Programmable answers.**  A test arranges what the page reports before
    walking the step body: :meth:`set_text`, :meth:`set_attribute`,
    :meth:`set_displayed`, :meth:`set_selected`, :meth:`set_tag_name`,
    :meth:`set_element_count`, :meth:`set_find_error` and the plain attributes
    :attr:`title`, :attr:`current_url`, :attr:`screenshot_png`,
    :attr:`screenshot_error`, :attr:`maximize_error`, :attr:`script_result` and
    :attr:`execute_result`.

    Every programming method takes ``locator=None`` to mean *any element*, and
    an optional ``index`` to single out one member of a ``find_elements``
    result.  Resolution is most specific first: ``(locator, index)``, then
    ``(locator, any index)``, then ``(any locator, any index)``.

    ``__slots__`` is declared, so assigning an attribute this class does not
    define - ``driver.titel = "x"`` - raises ``AttributeError`` instead of
    quietly creating a field no production code will ever read.  Nothing here
    is a coroutine and nothing here sleeps: a stub run is instantaneous.
    """

    __slots__ = (
        "_current_url",
        "_elements",
        "_find_errors",
        "_locator_counts",
        "_next_element_id",
        "_programmed",
        "_title",
        "calls",
        "execute_result",
        "maximize_error",
        "quit_count",
        "screenshot_error",
        "screenshot_png",
        "script_result",
    )

    def __init__(self, *, title: str = "", current_url: str = "") -> None:
        """Build an empty recorder.

        :param title: Initial value of :attr:`title`.  Empty by default, so a
            test asserting a title has to state the one it expects.
        :param current_url: Initial value of :attr:`current_url`.
        """
        #: The single ordered call log.  Mutable on purpose: a test may read it,
        #: slice it, or clear it with :meth:`clear_calls` between phases of a
        #: longer arrangement.
        self.calls: list[Call] = []

        self._title = title
        self._current_url = current_url

        #: Bytes returned by :meth:`get_screenshot_as_png`.  Assign ``b""`` to
        #: drive ``app/reporting/screenshots.py``'s unusable-payload branch.
        self.screenshot_png: Any = DEFAULT_SCREENSHOT_PNG

        #: Set to an exception (instance or class) to make
        #: :meth:`get_screenshot_as_png` raise, which is how
        #: ``test_screenshots.py`` exercises AAP deviation 19: a capture failure
        #: is logged and suppressed and the scenario's status is unchanged.
        self.screenshot_error: BaseException | type[BaseException] | None = None

        #: Set to an exception to make :meth:`maximize_window` raise.  The seam
        #: for ``app/automation/driver.py``'s documented ordering guarantee -
        #: the session is stored in the worker's slot *before* the window is
        #: maximized, so a maximize failure still leaves a session that
        #: ``quit_driver()`` can close rather than a leaked browser process.
        self.maximize_error: BaseException | type[BaseException] | None = None

        #: Value returned by :meth:`execute_script`.
        self.script_result: Any = None

        #: Value returned by :meth:`execute`.  The default is the shape
        #: Selenium's own command dispatch returns.
        self.execute_result: Any = {"value": None}

        #: How many times :meth:`quit` has been called.  ``quit`` is recorded
        #: and counted but changes nothing else: later calls are *not* made to
        #: raise, because whether touching a quit session is an error is the
        #: subject of some of these tests rather than a rule this stub imposes.
        self.quit_count = 0

        # Programmed answers, keyed most-specific-first.  One dict of tables
        # rather than five attributes, so adding a table cannot outrun
        # ``__slots__``.
        self._programmed: dict[str, dict[tuple[Any, ...], Any]] = {
            "texts": {},
            "attributes": {},
            "displayed": {},
            "selected": {},
            "tag_names": {},
        }
        self._locator_counts: dict[Locator, int] = {}
        self._find_errors: dict[Locator | None, BaseException | type[BaseException]] = {}
        self._elements: dict[Locator, list[StubElement]] = {}
        self._next_element_id = 0

    # -- internals shared with StubElement --------------------------------- #

    def _record(self, operation: str, *args: Any) -> None:
        """Append one entry to the ordered log.

        :param operation: Operation name; element operations arrive already
            prefixed with :data:`ELEMENT_PREFIX`.
        :param args: The operation's arguments, stored as a tuple exactly as
            they were passed.
        :returns: ``None``.
        """
        self.calls.append((operation, args))

    def _record_element(self, element: StubElement, operation: str, *args: Any) -> None:
        """Append an element operation, tagged with its originating locator.

        :param element: The element the operation was performed on.
        :param operation: Bare operation name, such as ``"click"``.
        :param args: The operation's own arguments, appended after the locator.
        :returns: ``None``.
        """
        self._record(f"{ELEMENT_PREFIX}{operation}", element.locator, *args)

    def _programmed_value(
        self,
        table: str,
        locator: Locator,
        index: int,
        key: str | None,
        default: Any,
    ) -> Any:
        """Resolve a programmed value, most specific entry first.

        :param table: Which table to consult - one of ``texts``,
            ``attributes``, ``displayed``, ``selected`` or ``tag_names``.
        :param locator: The reading element's locator.
        :param index: The reading element's index.
        :param key: A further discriminator, used only by ``attributes`` where
            it is the attribute name.
        :param default: Value returned when no entry matches.
        :returns: The most specific programmed value, or *default*.
        """
        entries = self._programmed[table]
        suffix: tuple[Any, ...] = () if key is None else (key,)

        for scope in ((locator, index), (locator, None), (None, None)):
            found = entries.get((*scope, *suffix), _UNSET)
            if found is not _UNSET:
                return found

        return default

    def _store(
        self,
        table: str,
        locator: Locator | None,
        index: int | None,
        key: str | None,
        value: Any,
    ) -> None:
        """Record a programmed value in one of the tables.

        :param table: Table name, as in :meth:`_programmed_value`.
        :param locator: Locator the value applies to, or ``None`` for any.
        :param index: Element index it applies to, or ``None`` for any.
        :param key: Attribute name, for the ``attributes`` table only.
        :param value: The value to report.
        :returns: ``None``.
        :raises KeyError: If *table* is not one of the five, which turns a typo
            in a test's arrangement into an immediate failure.
        """
        entries = self._programmed[table]
        suffix: tuple[Any, ...] = () if key is None else (key,)

        # A locator-less entry is the "any element" fallback, and
        # :meth:`_programmed_value` only ever consults it as ``(None, None)``.
        # Normalising here means ``locator=None, index=2`` cannot be stored
        # under a key nothing would ever read.
        scope = (None, None) if locator is None else (locator, index)
        entries[(*scope, *suffix)] = value

    def _element(self, locator: Locator, index: int) -> StubElement:
        """Return the cached element for *locator* at *index*, creating it once.

        Caching is the behaviour under test in one specific respect: page-object
        accessors resolve **on every access with no caching** (the un-cached
        ``@FindBy`` proxy of ``LoginP.java:9-11``), so the log must show a
        ``find_element`` entry per access.  It does - every call is recorded
        before this method is reached.  What caching gives is a *stable*
        element, so a value programmed once keeps applying and a test may
        compare identity across accesses to demonstrate that the driver, not
        the page object, is what the lookups reach.

        :param locator: The pair that produced the element.
        :param index: Position among that locator's elements.
        :returns: The cached element.
        """
        cached = self._elements.setdefault(locator, [])

        while len(cached) <= index:
            self._next_element_id += 1
            cached.append(
                StubElement(self, locator, len(cached), f"stub-element-{self._next_element_id}")
            )

        return cached[index]

    def _check_find_error(self, locator: Locator) -> None:
        """Raise a programmed lookup failure, if one applies to *locator*.

        :param locator: The locator being looked up.
        :returns: ``None`` when no failure is programmed.
        :raises BaseException: The programmed error, most specific first.
        """
        for scope in (locator, None):
            error = self._find_errors.get(scope)
            if error is not None:
                _raise(error)

    # -- the WebDriver surface --------------------------------------------- #

    def get(self, url: str) -> None:
        """Record a navigation.

        The single most asserted operation in the parity suite: every step that
        navigates does so to a URL taken from ``app/config.py``, and the test
        asserts both the operation's position and the value.

        :param url: Target URL, logged exactly as supplied.
        :returns: ``None``.
        """
        self._record("get", url)

    def find_element(self, by: str, value: str) -> StubElement:
        """Record a singular lookup and return the element for that locator.

        :param by: Locator strategy - a ``By`` member, which is a plain string.
        :param value: Locator value.
        :returns: The cached :class:`StubElement` for ``(by, value)``.
        :raises BaseException: A failure programmed with :meth:`set_find_error`.
            The call is logged *before* the failure is raised, because the
            lookup was attempted and a test asserting the sequence needs to see
            that it was.
        """
        self._record("find_element", by, value)
        locator = (by, value)
        self._check_find_error(locator)
        return self._element(locator, 0)

    def find_elements(self, by: str, value: str) -> list[StubElement]:
        """Record a plural lookup and return that locator's elements.

        :param by: Locator strategy.
        :param value: Locator value.
        :returns: A new list holding the locator's elements - one by default,
            or however many :meth:`set_element_count` declared, and empty when
            that count is zero.  A fresh list each call, so a caller that
            mutates the result cannot disturb the cache.
        :raises BaseException: A failure programmed with :meth:`set_find_error`.
        """
        self._record("find_elements", by, value)
        locator = (by, value)
        self._check_find_error(locator)
        count = self._locator_counts.get(locator, 1)
        return [self._element(locator, index) for index in range(count)]

    @property
    def title(self) -> str:
        """The page title, recorded on every read.

        The seam ``test_steps_employee.py`` needs - ``EmployeeStage.java:31``
        compares the title against the ``EmplTitle`` property, so the test
        programmes ``driver.title = "Employees - Odoo"`` and asserts the
        comparison - and the one ``test_steps_login.py`` needs for the Odoo
        dashboard assertion at ``LoginSD.java:44-46``.

        :returns: The current value.
        """
        self._record("title")
        return self._title

    @title.setter
    def title(self, value: str) -> None:
        """Programme the page title.

        Assignment is *not* logged: arranging what the page says is the test's
        setup, not an operation the code under test performed.

        :param value: The title to report.
        :returns: ``None``.
        """
        self._title = value

    @property
    def current_url(self) -> str:
        """The current URL, recorded on every read.

        :returns: The current value.
        """
        self._record("current_url")
        return self._current_url

    @current_url.setter
    def current_url(self, value: str) -> None:
        """Programme the current URL, without logging the assignment.

        :param value: The URL to report.
        :returns: ``None``.
        """
        self._current_url = value

    def get_screenshot_as_png(self) -> Any:
        """Record a capture and return the programmed payload.

        The Python binding that replaces Java's
        ``getScreenshotAs(OutputType.BYTES)`` (``Hooks.java:14``).  Returns a
        real, deterministic 1x1 PNG by default, so a test can assert a base64
        round-trip without pinning platform-dependent bytes.

        :returns: :attr:`screenshot_png`.
        :raises BaseException: :attr:`screenshot_error`, when one is set.  The
            capture is logged first, so a test proving that the failure was
            suppressed can also prove that it was attempted.
        """
        self._record("get_screenshot_as_png")

        if self.screenshot_error is not None:
            _raise(self.screenshot_error)

        return self.screenshot_png

    def maximize_window(self) -> None:
        """Record the window maximization of ``Driver.java:33``.

        :returns: ``None``.
        :raises BaseException: :attr:`maximize_error`, when one is set.
        """
        self._record("maximize_window")

        if self.maximize_error is not None:
            _raise(self.maximize_error)

    def implicitly_wait(self, seconds: float) -> None:
        """Record the implicit wait of ``Driver.java:34``.

        The argument is logged unconverted.  The Python binding takes
        *seconds*, so the port's literal is ``10`` and never a millisecond
        value, and a test asserts that number here.

        :param seconds: Timeout in seconds.
        :returns: ``None``.
        """
        self._record("implicitly_wait", seconds)

    def quit(self) -> None:
        """Record a session teardown and count it.

        :returns: ``None``.
        """
        self._record("quit")
        self.quit_count += 1

    def execute_script(self, script: str, *args: Any) -> Any:
        """Record a script execution and return the programmed result.

        :param script: The script source, logged verbatim.
        :param args: Script arguments, logged after it.
        :returns: :attr:`script_result`.
        """
        self._record("execute_script", script, *args)
        return self.script_result

    def back(self) -> None:
        """Record a browser back navigation.

        Two step bodies use it, so it is part of the surface rather than a
        convenience.

        :returns: ``None``.
        """
        self._record("back")

    def execute(self, driver_command: str, params: Any = None) -> Any:
        """Record a raw command dispatch and return the programmed result.

        Selenium's own command channel, present because
        ``ActionChains(...).perform()`` dispatches through it: with this method
        a keyboard-only action chain built on this stub can be performed and
        asserted.  Note the measured limit, so no test is written against an
        expectation that cannot hold - selenium's pointer actions type-check
        their target with ``isinstance(..., WebElement)`` and reject a duck-typed
        element outright, so an *element-targeted* chain cannot be driven by a
        stub at all and ``app/automation/interactions.py``'s ``action_chain``
        seam is asserted on the chain it returns rather than on a performed
        pointer move.

        :param driver_command: The command name.
        :param params: The command's parameters.
        :returns: :attr:`execute_result`.
        """
        self._record("execute", driver_command, params)
        return self.execute_result

    # -- programming the page's answers ------------------------------------ #

    def set_text(
        self, locator: Locator | None, value: str, *, index: int | None = None
    ) -> None:
        """Programme the visible text an element reports.

        :param locator: The locator it applies to, or ``None`` for any element.
        :param value: The text to report.
        :param index: Restrict to one member of a ``find_elements`` result;
            ``None`` applies to every member. Ignored when *locator* is ``None``.
        :returns: ``None``.
        """
        self._store("texts", locator, index, None, value)

    def set_attribute(
        self,
        locator: Locator | None,
        name: str,
        value: Any,
        *,
        index: int | None = None,
    ) -> None:
        """Programme one attribute of an element.

        :param locator: The locator it applies to, or ``None`` for any element -
            which is the convenient form for ``set_attribute(None, "type",
            "password")``, an assertion about whichever input the step happened
            to resolve.
        :param name: Attribute name, matched exactly as ``get_attribute``
            passes it.
        :param value: The value to report; ``None`` is a legitimate programmed
            value and stays distinguishable from "not programmed".
        :param index: Restrict to one member of a plural result.
        :returns: ``None``.
        """
        self._store("attributes", locator, index, name, value)

    def set_displayed(
        self, locator: Locator | None, value: bool, *, index: int | None = None
    ) -> None:
        """Programme what ``is_displayed()`` reports.

        :param locator: The locator it applies to, or ``None`` for any element.
        :param value: The visibility to report.
        :param index: Restrict to one member of a plural result.
        :returns: ``None``.
        """
        self._store("displayed", locator, index, None, value)

    def set_selected(
        self, locator: Locator | None, value: bool, *, index: int | None = None
    ) -> None:
        """Programme what ``is_selected()`` reports.

        :param locator: The locator it applies to, or ``None`` for any element.
        :param value: The selection state to report.
        :param index: Restrict to one member of a plural result.
        :returns: ``None``.
        """
        self._store("selected", locator, index, None, value)

    def set_tag_name(
        self, locator: Locator | None, value: str, *, index: int | None = None
    ) -> None:
        """Programme what ``tag_name`` reports.

        :param locator: The locator it applies to, or ``None`` for any element.
        :param value: The tag name to report.
        :param index: Restrict to one member of a plural result.
        :returns: ``None``.
        """
        self._store("tag_names", locator, index, None, value)

    def set_element_count(self, locator: Locator, count: int) -> None:
        """Declare how many elements a plural lookup yields.

        The seam for the port's one ``List<WebElement>`` field,
        ``SalesP.java:69``, reached through ``app/pages/sales_page.py``'s
        ``PLURAL_LOCATORS``.

        :param locator: The locator whose result size is being declared.
        :param count: Number of elements to return; ``0`` yields an empty list,
            which is what Selenium returns when nothing matches.
        :returns: ``None``.
        :raises ValueError: If *count* is negative, which no lookup can produce.
        """
        if count < 0:
            raise ValueError(f"element count cannot be negative: {count!r}")

        self._locator_counts[locator] = count

    def set_find_error(
        self, locator: Locator | None, error: BaseException | type[BaseException]
    ) -> None:
        """Make a lookup fail, as the real driver's would.

        ``app/pages/base_page.py`` documents that ``find`` catches, wraps and
        retries nothing - "whatever the driver raises" reaches the caller,
        typically once the session's 10-second implicit wait expires - so this
        is how a test covers that path without importing selenium's exception
        types.

        :param locator: The locator that should fail, or ``None`` to fail every
            lookup.
        :param error: The exception to raise, as an instance or a class.
        :returns: ``None``.
        """
        self._find_errors[locator] = error

    # -- reading the log --------------------------------------------------- #

    def operations(self) -> tuple[str, ...]:
        """The operation names in order, with arguments dropped.

        The readable form of a sequence assertion: comparing against
        ``("get", "find_element", "element.send_keys")`` states the shape of an
        interaction without restating every argument, which a separate
        assertion on :meth:`calls_of` then pins.

        :returns: The operation names in call order.
        """
        return tuple(operation for operation, _ in self.calls)

    def calls_of(self, operation: str) -> tuple[tuple[Any, ...], ...]:
        """Every argument tuple recorded for one operation, in order.

        :param operation: The operation name, element operations included with
            their :data:`ELEMENT_PREFIX`.
        :returns: One argument tuple per occurrence, in call order.
        """
        return tuple(args for name, args in self.calls if name == operation)

    def count_of(self, operation: str) -> int:
        """How many times one operation was recorded.

        The direct expression of the no-caching guarantee: a page-object
        accessor read three times must produce three ``find_element`` calls.

        :param operation: The operation name.
        :returns: The number of occurrences.
        """
        return sum(1 for name, _ in self.calls if name == operation)

    def clear_calls(self) -> None:
        """Empty the log, keeping every programmed answer in place.

        For a test that arranges a page, runs a precondition step, and then
        wants the log to describe only the step actually under assertion.

        :returns: ``None``.
        """
        self.calls.clear()

    def __repr__(self) -> str:
        """Summarise the log length, which is what a failure needs first.

        :returns: A short representation.
        """
        return f"<StubDriver calls={len(self.calls)} title={self._title!r}>"


@pytest.fixture
def stub_driver() -> StubDriver:
    """A fresh :class:`StubDriver` with an empty log and nothing programmed.

    Function-scoped without exception: the log and the programmed answers are
    mutable state, and sharing either across tests is precisely the leak this
    suite is built to avoid.

    :returns: A new recorder.
    """
    return StubDriver()


# =========================================================================== #
# The behave context stand-in
#
# The port's step modules take everything they need from behave's ``context``,
# and the census of what they actually touch is short and was measured against
# the tree rather than guessed: ``context.driver`` (86 sites),
# ``context.config.userdata`` (3), and ``context.attach`` (3).  Those three,
# plus arbitrary attribute assignment, are the whole contract reproduced here.
#
# Why a stand-in is the *only* supported way to drive a step body: no step
# module builds a page object at import time, because a module-level
# construction would bind whichever worker process imported the module first.
# Each one builds its page per call from ``context.driver`` instead - see any
# ``_page(context)`` helper under ``features/steps/`` - so a test that wants to
# reach a step body has to hand it a context, and this is that context.
# =========================================================================== #


def _snake_case(name: str) -> str:
    """Convert a page class name to the attribute name :meth:`attach_page` uses.

    ``LoginPage`` becomes ``login_page`` and ``LogOutPage`` becomes
    ``log_out_page`` - the capital ``O`` is preserved as a word boundary
    because the class name preserves it, mirroring the Java ``LogOutP``.

    :param name: A class name in ``CamelCase``.
    :returns: The same name in ``snake_case``.
    """
    characters: list[str] = []

    for position, character in enumerate(name):
        if character.isupper() and position and not name[position - 1].isupper():
            characters.append("_")

        characters.append(character.lower())

    return "".join(characters)


class FakeConfig:
    """Stands in for ``context.config``, carrying the userdata mapping.

    A plain class, so any attribute may be assigned and reading one that was
    never set raises ``AttributeError`` - the same way behave's own config
    object behaves, and the reason no permissive ``__getattr__`` is defined
    here: returning ``None`` for an unset name would let a test misspell an
    attribute and still pass.
    """

    def __init__(self, userdata: Mapping[str, str] | None = None) -> None:
        """Build a config carrying a mutable userdata mapping.

        :param userdata: Initial userdata, copied so the caller's mapping is
            never aliased.  ``None`` yields an empty mapping.
        """
        #: The ``-D key=value`` channel.  ``app/cli.py`` passes ``--browser``
        #: to each worker this way, ``features/environment.py``'s ``before_all``
        #: hands this mapping to ``app.config.set_userdata``, and
        #: ``app/config.py`` reads it *ahead of* the properties file (AAP
        #: 0.4.1).  A plain ``dict`` rather than behave's own ``UserData``
        #: subclass, because every consumer in this port treats it as a bare
        #: ``Mapping[str, str]`` and a plain dict keeps behave out of this
        #: module's imports.
        self.userdata: MutableMapping[str, str] = dict(userdata or {})


class FakeContext:
    """Stands in for behave's ``Context`` for the duration of one test.

    Arbitrary attributes may be set, which matters because a step body is free
    to stash whatever it needs on the context between steps of a scenario.
    Reading an attribute that was never set raises ``AttributeError``, matching
    behave and keeping a typo a failure.
    """

    def __init__(
        self,
        driver: Any = None,
        *,
        userdata: Mapping[str, str] | None = None,
    ) -> None:
        """Build a context.

        :param driver: The session published as :attr:`driver`, which is what
            ``features/environment.py``'s ``before_scenario`` does with
            ``get_driver()``'s result and what every step body reads.
        :param userdata: Initial ``context.config.userdata`` contents.
        """
        #: The behave config, carrying ``userdata``.
        self.config = FakeConfig(userdata)

        #: This scenario's session.  ``None`` is a legitimate value and is not
        #: substituted for: ``after_scenario`` sets it back to ``None``, and
        #: ``app/pages/base_page.py`` passes a ``None`` driver through
        #: unchanged so that the failure happens at the point of use exactly as
        #: it does in Java.
        self.driver = driver

        #: Every ``attach`` call, in order, as ``(mime_type, data)`` pairs.
        self.attachments: list[tuple[str, Any]] = []

    def attach(self, mime_type: str, data: Any) -> None:
        """Record an attachment, mirroring ``Context.attach(mime_type, data)``.

        behave's real method forwards to whichever formatters expose an
        ``embedding`` hook; recording the pair is the observable half of that
        and is what ``features/environment.py``'s failure path is asserted on.
        Note the two-argument signature: behave has **no** name parameter, which
        is why the port's event collector supplies the third argument of the
        Java ``attach`` call - the scenario name - from the scenario it is
        already tracking.

        :param mime_type: MIME type of the payload, ``"image/png"`` for the
            port's only attachment.
        :param data: The payload, raw bytes for a screenshot.
        :returns: ``None``.
        """
        self.attachments.append((mime_type, data))

    def use_with_user_mode(self) -> contextlib.AbstractContextManager[FakeContext]:
        """Return a no-op context manager, for compatibility with behave.

        behave's ``Match.run(context)`` wraps the call in
        ``context.use_with_user_mode()``, which on the real context switches
        the attribute layer between behave's own mode and the user's.  There are
        no layers here - attributes are plain instance attributes - so this
        yields the context unchanged and exists purely so that a test may drive
        a step through behave's own ``Match.run`` if it prefers that to
        :meth:`StepMatch.run`.

        :returns: A context manager yielding this context.
        """
        return contextlib.nullcontext(self)

    def attach_page(self, page: Any, *, name: str | None = None) -> Any:
        """Publish a page object on the context and return it.

        :param page: A page *class*, which is constructed with :attr:`driver` so
            that the resulting object's driver is this context's - normally the
            :fixture:`stub_driver` - or an already-built object, which is stored
            as it is.  Constructing from the class is the form to prefer: it is
            the only one that guarantees the binding, and ``BasePage`` accepts a
            driver positionally or by keyword and touches neither the DOM nor
            the driver at construction.
        :param name: Attribute name to publish under.  Defaults to the class
            name in snake case, so :class:`LoginPage` lands on
            ``context.login_page``.
        :returns: The page object, so a test can bind it in one line.

        A convenience, not a requirement: the port's step modules never read a
        page object off the context - each builds its own per call from
        ``context.driver`` - so this exists for a test that wants a page bound to
        the same recorder in order to arrange or inspect it alongside the step.
        """
        instance = page(self.driver) if isinstance(page, type) else page
        attribute = name or _snake_case(type(instance).__name__)
        setattr(self, attribute, instance)
        return instance

    def __repr__(self) -> str:
        """Summarise the driver and attachment count.

        :returns: A short representation.
        """
        return (
            f"<FakeContext driver={self.driver!r} "
            f"attachments={len(self.attachments)}>"
        )


@pytest.fixture
def fake_context(stub_driver: StubDriver) -> FakeContext:
    """A behave context whose session is the :fixture:`stub_driver`.

    The pairing every parity test starts from: the context carries the recorder,
    so a step body's page lookups, navigations and reads all land in one ordered
    log that the test then asserts against the Java original.

    ``context.config.userdata`` starts empty.  A test needing the ``--browser``
    override path populates it and hands it to ``app.config.set_userdata``
    itself, per test, rather than relying on process-wide state - see the
    isolation fixture's note on why userdata is deliberately not reset globally.

    :param stub_driver: The recorder published as ``context.driver``.
    :returns: A fresh context.
    """
    return FakeContext(driver=stub_driver)


# =========================================================================== #
# The artifact-path seam
#
# ``app/utils/paths.py`` owns every artifact path in this port, and each of its
# accessors takes ``base``, defaulting to the process working directory.  That
# parameter is the declared unit-test seam and the fixtures below are how this
# suite uses it: a writer test builds its artifacts under a per-test temporary
# directory, so **no test ever writes into the repository's real ``target/``**.
#
# Nothing here changes the working directory, and no test should.  ``chdir``
# leaks into whatever runs next in the same process - pytest does not restore it
# between tests - and it would make the suite order-dependent for no gain, since
# passing ``base=`` is both explicit and local.  It would also silently defeat
# the one behaviour ``app/utils/paths.py`` documents about its default: the
# working directory is read fresh on every call, precisely so a worker process
# that moved sees the move.
# =========================================================================== #

#: Name of the directory created inside pytest's ``tmp_path`` to stand in for a
#: checkout root.  A named subdirectory rather than ``tmp_path`` itself, so that
#: a test may still use ``tmp_path`` for unrelated scratch files - a properties
#: file, say - without those files appearing beside the artifact tree.
WORKSPACE_DIR_NAME: Final[str] = "workspace"


@pytest.fixture
def tmp_artifact_root(tmp_path: Path) -> Path:
    """A private, empty directory to pass as ``base=`` to path-taking code.

    Use it everywhere a path is involved::

        json_path = paths.cucumber_json_path(tmp_artifact_root)
        write_cucumber_json(results, base=tmp_artifact_root)

    Derived from pytest's ``tmp_path``, so it is unique per test - two tests in
    one session never share one - and it is left in place after the run for
    inspection under pytest's own retention policy.

    Nothing is created inside it: ``target/`` is absent until the code under
    test creates it, which is what lets a test assert that a writer creates its
    own parent directory.  :fixture:`prepared_artifact_root` is the variant for
    tests that need it to exist beforehand.

    :param tmp_path: pytest's per-test temporary directory.
    :returns: An existing, empty directory standing in for a checkout root.
    """
    root = tmp_path / WORKSPACE_DIR_NAME
    root.mkdir()
    return root


@pytest.fixture
def prepared_artifact_root(tmp_artifact_root: Path) -> Path:
    """:fixture:`tmp_artifact_root` with an empty ``target/`` already created.

    The convenience for a test whose subject writes *into* ``target/`` and is
    not itself responsible for creating it.  The directory is made through
    ``app.utils.paths.ensure_dir``, the port's own helper, rather than
    ``mkdir`` - so the fixture exercises the same code path production does and
    cannot drift from it.

    ``target/.workers/`` is deliberately **not** created.  That directory's
    whole lifecycle is under test: ``app/services/test_run_service.py`` creates
    it before a run, ``--clean`` empties it, and it is removed after the merge
    whether the merge succeeded or failed, so that no intermediate JSON is ever
    visible to the Jenkins publisher (AAP 0.4.1).  ``test_cli.py`` and
    ``test_test_run_service.py`` assert that lifecycle, and a fixture that
    pre-created the directory would invalidate every one of those assertions.

    :param tmp_artifact_root: The temporary checkout root.
    :returns: The same root, with ``target/`` present and empty.
    """
    paths.ensure_dir(paths.target_root(tmp_artifact_root))
    return tmp_artifact_root


# =========================================================================== #
# The fixture-data loaders
#
# Three files, all read by path and never reproduced as literal content here -
# a copy in this module would fork the baseline the moment either side was
# edited, and the point of a baseline is that there is one of it.
#
# Both golden files are stored **verbatim**, legacy
# ``src/main/resources/features/...`` URIs included.  The port emits
# ``features/...`` instead, because AAP deviation 1 moved the feature directory
# while preserving the filenames, and that is **the one expected difference**
# between a golden baseline and a freshly written artifact.  Reconciling it is
# production code: ``app.utils.paths.normalize_feature_uri`` together with
# ``LEGACY_FEATURES_PREFIX``.  The two helpers below *delegate* to it and to
# nothing else, so this suite contains no second implementation of the rule and
# no test hard-codes either prefix.
# =========================================================================== #

#: The keys that carry a feature path in the two schemas this suite handles:
#: ``uri`` in the Cucumber-JVM contract, and ``uri`` plus ``path`` in the port's
#: internal result schema.  Named here so the walk below rewrites a bounded set
#: rather than every string it encounters.
#:
#: ``error_message`` is excluded on purpose, and the exclusion is load-bearing
#: rather than an oversight: the legacy prefix genuinely occurs inside the
#: golden report's two failure messages, because a Java stack trace names the
#: feature it came from.  Rewriting it there would suggest those messages are
#: comparable against the port's output, and they are not - AAP deviation 16
#: states that ``error_message`` carries Python assertion text and a Python
#: traceback, and that only the assertion's *subject and message* are parity
#: while their surrounding formatting is not.  A writer test therefore asserts
#: on the presence and the subject of a failure message, never on the golden
#: text of one.
FEATURE_URI_KEYS: Final[frozenset[str]] = frozenset({"uri", "path"})


def _read_fixture_text(path: Path) -> str:
    """Read one fixture file as text.

    :param path: The file to read.
    :returns: Its contents, decoded as :data:`FIXTURE_ENCODING`.
    :raises FileNotFoundError: If the file is absent.  Raised rather than
        smoothed over: these three files are the suite's baselines, and a test
        silently comparing against a default would prove nothing at all.
    """
    return path.read_text(encoding=FIXTURE_ENCODING)


def _load_fixture_json(path: Path) -> Any:
    """Read and parse one JSON fixture file.

    :param path: The file to read.
    :returns: The parsed document - a ``list`` for the golden Cucumber report, a
        ``dict`` for the internal result set.
    :raises FileNotFoundError: If the file is absent.
    :raises json.JSONDecodeError: If it does not parse, which for the golden
        report would mean the merge-conflict surgery AAP 0.6 describes had been
        undone.
    """
    return json.loads(_read_fixture_text(path))


def normalize_feature_uris(document: Any) -> Any:
    """Rewrite legacy feature-directory prefixes throughout a parsed document.

    Walks mappings and sequences, and for every value held under one of
    :data:`FEATURE_URI_KEYS` hands the string to
    ``app.utils.paths.normalize_feature_uri``.  That function - not this one -
    owns the rule; it is idempotent, it preserves a leading ``file:`` scheme,
    and it leaves anything that does not start with the legacy prefix untouched,
    so applying it to an already-normalised document changes nothing.

    :param document: A parsed JSON document, or any nested structure of
        mappings, sequences and scalars.
    :returns: A new structure with the prefixes rewritten.  The input is never
        mutated, which matters because the loader fixtures hand out documents
        that other assertions in the same test may still be comparing against.
    """
    if isinstance(document, Mapping):
        return {
            key: (
                paths.normalize_feature_uri(value)
                if key in FEATURE_URI_KEYS and isinstance(value, str)
                else normalize_feature_uris(value)
            )
            for key, value in document.items()
        }

    if isinstance(document, (list, tuple)):
        return [normalize_feature_uris(item) for item in document]

    return document


def normalize_rerun_manifest(text: str) -> str:
    """Rewrite legacy feature-directory prefixes in a rerun manifest.

    The manifest is line-oriented - one line per feature, the failing line
    numbers appended colon-separated (AAP 0.6) - and
    ``app.utils.paths.normalize_feature_uri`` is documented as preserving
    anything that follows the path, the ``:9:24`` suffix included, because it
    substitutes a prefix rather than parsing the line.  So each line is handed
    to it whole.

    :param text: Manifest contents.
    :returns: The same text with each line's prefix rewritten.  Line structure
        is preserved exactly, the trailing newline included, so a comparison
        against a writer's output still tests the byte-level shape of the format
        - which is load-bearing here, since a second runner reads this file as
        machine input (``FailedTestRunner.java:11``).
    """
    return "".join(
        paths.normalize_feature_uri(line) if line else line
        for line in text.splitlines(keepends=True)
    )


@pytest.fixture
def golden_rerun() -> str:
    """The committed ``target/rerun.txt``, verbatim.

    Measured properties of this baseline, all of them load-bearing for
    ``test_rerun_report.py``: 50 bytes, a single line, a trailing newline
    present, and free of merge-conflict markers - the only one of the committed
    artifacts that needed no surgery.  Its content is one feature with two
    failing line numbers appended to it, which is the whole of the format the
    port must reproduce.

    Compare against :fixture:`golden_rerun_normalized` rather than this value,
    unless the assertion is specifically about the legacy prefix.

    :returns: The manifest text.
    """
    return _read_fixture_text(GOLDEN_RERUN_PATH)


@pytest.fixture
def golden_rerun_normalized(golden_rerun: str) -> str:
    """:fixture:`golden_rerun` with the feature-directory prefix rewritten.

    What a rerun-manifest assertion actually compares against: identical to the
    committed baseline in every respect except the one difference AAP deviation
    1 requires, and reached through the production normalizer so that no test
    names either prefix.

    :param golden_rerun: The verbatim manifest.
    :returns: The manifest with ``features/`` in place of the legacy prefix.
    """
    return normalize_rerun_manifest(golden_rerun)


@pytest.fixture
def golden_cucumber() -> Any:
    """The golden ``target/cucumber.json``, parsed.

    This baseline is the ``HEAD`` side of the committed artifact **taken
    alone**.  The committed file carries one unresolved merge-conflict block
    whose two sides do not concatenate into valid JSON, so "strip the markers"
    is not the instruction and was not what was done: one side is the baseline
    and the other - a ``feature/Contact`` variant - is discarded (AAP 0.6).

    Measured shape, which ``test_cucumber_json.py`` can rely on: a list holding
    exactly one feature, *Testinium app CRM Module*, at uri
    ``file:src/main/resources/features/Crm.feature``, whose ``elements`` list
    holds eight entries interleaved background, scenario, background, scenario,
    background, scenario, background, scenario - the Background repeated once
    before each scenario, which is the positional invariant this suite pins.

    Re-parsed for every test that asks for it, so a test that edits the document
    in place cannot affect the next one.

    :returns: The parsed feature list.
    """
    return _load_fixture_json(GOLDEN_CUCUMBER_PATH)


@pytest.fixture
def golden_cucumber_normalized(golden_cucumber: Any) -> Any:
    """:fixture:`golden_cucumber` with feature URIs rewritten to ``features/``.

    :param golden_cucumber: The verbatim baseline.
    :returns: The same document with the one expected difference applied.
    """
    return normalize_feature_uris(golden_cucumber)


@pytest.fixture
def sample_result_set() -> Any:
    """The hand-built merged result set, parsed.

    Written in the port's **internal** schema - the shape
    ``app/reporting/events.py`` owns and every writer consumes - rather than in
    any published format, and it is the shared input to all four writer tests so
    that the four artifacts of one run are asserted against one another's source
    of truth.

    It is built to reach every writer branch: a passing scenario, a failing
    scenario carrying a screenshot embedding, a skipped step, an undefined step,
    a background, and a two-row outline.  Its top level carries
    ``schema_version``, ``started_at``, ``generated_at``, ``dry_run``,
    ``tag_expression``, ``metadata`` and ``features``.

    Re-parsed per test, for the same isolation reason as
    :fixture:`golden_cucumber`.

    :returns: The parsed result set.
    """
    return _load_fixture_json(SAMPLE_RESULTS_PATH)


# =========================================================================== #
# The step registry
#
# Eleven test modules need phrase-to-function resolution done the way the engine
# does it: ``test_steps_registration.py``, which proves that every phrase across
# all ten feature files resolves to exactly one implementation and that no
# phrase resolves to more than one, and the ten ``test_steps_<area>.py`` parity
# modules, which reach each step body through it.
#
# behave's own loader and registry are used rather than a reimplementation of
# discovery, and the API below was verified against the installed behave 1.3.3
# rather than taken from documentation:
#
#   * ``behave.runner_util.load_step_modules(step_paths)`` execs every ``*.py``
#     in the given directories, which is how a module-scope
#     ``register_type(CukeStr=...)`` - ``features/steps/contacts_steps.py``
#     declares one - is in force before the decorators that use it run;
#   * ``behave.step_registry.registry`` is the process-wide ``StepRegistry``,
#     whose ``steps`` dict holds the four buckets ``given``, ``when``, ``then``
#     and ``step``.  Measured against this tree, all 91 definitions land in
#     ``step`` and the other three are empty - which is AAP deviation 7 holding;
#   * a matcher exposes ``pattern``, ``location`` and ``match(text)``, the last
#     returning a ``Match`` with ``func`` and ``arguments``, or ``None``.
#
# Loading happens exactly once per session.  Re-loading is in fact tolerated -
# ``StepRegistry.same_step_definition`` recognises an identical registration and
# returns - but only for a *byte-identical* re-registration; anything else
# raises ``AmbiguousStep``, so once is what this fixture does.
# =========================================================================== #

#: Sub-directory of ``features/`` holding the ten step modules, which is where
#: the engine discovers them.
STEPS_DIR_NAME: Final[str] = "steps"

#: The loaded registry, cached at module scope so that the session fixture and a
#: direct call to :func:`load_step_registry` share one load rather than racing to
#: perform two.  A one-entry dict rather than a rebindable module variable, so
#: the loader needs no ``global`` statement: mutating a container is not the
#: same hazard as rebinding module state, and the cache has exactly one writer.
_REGISTRY_CACHE: Final[dict[str, Any]] = {}

#: The only key :data:`_REGISTRY_CACHE` ever carries.
_REGISTRY_CACHE_KEY: Final[str] = "registry"


def step_modules_dir() -> Path:
    """The directory the step modules are loaded from.

    Resolved as ``app.utils.paths.features_dir(REPO_ROOT) / "steps"``: through
    the path module, which owns every path in this port, and against the
    repository root explicitly rather than the working directory.  The explicit
    base is the point - in production the engine resolves ``features/`` relative
    to where the run was launched, but a unit suite that inherited that would
    pass or fail according to the directory pytest happened to be started from.

    :returns: The absolute path of ``features/steps``.
    """
    return paths.features_dir(REPO_ROOT) / STEPS_DIR_NAME


def load_step_registry() -> Any:
    """Load the port's step modules once and return behave's step registry.

    Idempotent by caching: the first call loads, every later call returns the
    same registry without touching the filesystem.  behave is imported here
    rather than at module scope, so a test that never resolves a step phrase
    never pays for the engine.

    :returns: behave's process-wide ``StepRegistry``, populated with this port's
        definitions.
    :raises FileNotFoundError: If ``features/steps`` is absent, which would mean
        the step modules had not been created.
    """
    cached = _REGISTRY_CACHE.get(_REGISTRY_CACHE_KEY)

    if cached is not None:
        return cached

    from behave.runner_util import load_step_modules
    from behave.step_registry import registry

    steps_dir = step_modules_dir()

    if not steps_dir.is_dir():
        raise FileNotFoundError(f"step module directory not found: {steps_dir}")

    load_step_modules([str(steps_dir)])
    _REGISTRY_CACHE[_REGISTRY_CACHE_KEY] = registry
    return registry


class StepMatch(NamedTuple):
    """One resolved step: the function to call and the arguments to call it with.

    The parity modules' entry point into a step body.  :meth:`run` performs the
    call, so a test reads as the scenario reads - a phrase, then the assertions
    about what it did to the recorder.
    """

    #: The Gherkin phrase that was resolved.
    phrase: str

    #: The step function itself, which a test may also inspect - its
    #: ``__name__`` is asserted against the Java method name it ports.
    func: Callable[..., Any]

    #: Positional argument values, for a pattern whose groups are unnamed.
    args: tuple[Any, ...]

    #: Named argument values, which is the form every definition in this port
    #: produces because every parameter is written ``{name}`` or
    #: ``{name:CukeStr}``.
    kwargs: dict[str, Any]

    #: The definition's ``pattern`` - the text inside its ``@step`` decorator.
    pattern: str

    #: Rendered source location, ``<file>:<line>``.  behave renders the filename
    #: relative to the working directory, so assert on :attr:`module_name`
    #: rather than on this when the question is which module owns the step.
    location: str

    #: The owning step module's name without its extension, such as
    #: ``login_steps``.  Derived from the location because the loaded functions
    #: carry no ``__module__``: ``load_step_modules`` execs the files rather
    #: than importing them.
    module_name: str

    #: The registry bucket it was registered in.  Always ``"step"`` in this
    #: port; anything else is a deviation-7 violation and is reported rather
    #: than tolerated.
    bucket: str

    def run(self, context: Any) -> Any:
        """Invoke the step body with *context* and the resolved arguments.

        Calls the function directly rather than going through behave's
        ``Match.run``, which additionally enters
        ``context.use_with_user_mode()``.  :class:`FakeContext` provides that
        method too, so either route works; this one keeps the call the test
        performs as close to a bare function call as it can be.

        :param context: The behave context to pass as the first argument -
            normally :fixture:`fake_context`.
        :returns: Whatever the step function returns, which for every step in
            this port is ``None``.
        """
        return self.func(context, *self.args, **self.kwargs)


def _build_step_match(phrase: str, matcher: Any, match: Any, bucket: str) -> StepMatch:
    """Assemble a :class:`StepMatch` from behave's matcher and match objects.

    :param phrase: The phrase that was resolved.
    :param matcher: The registered step matcher, which carries the pattern and
        the source location.
    :param match: behave's ``Match``, which carries the extracted arguments.
    :param bucket: The registry bucket the matcher came from.
    :returns: The assembled match.
    """
    args: list[Any] = []
    kwargs: dict[str, Any] = {}

    for argument in match.arguments:
        if argument.name is None:
            args.append(argument.value)
        else:
            kwargs[argument.name] = argument.value

    location = matcher.location
    filename = str(getattr(location, "filename", "") or "")

    return StepMatch(
        phrase=phrase,
        func=match.func,
        args=tuple(args),
        kwargs=kwargs,
        pattern=str(getattr(matcher, "pattern", "")),
        location=str(location),
        module_name=Path(filename).stem,
        bucket=bucket,
    )


def find_all_step_matches(phrase: str) -> tuple[StepMatch, ...]:
    """Every step definition that matches *phrase*, across all four buckets.

    The ambiguity check ``test_steps_registration.py`` needs.  ``@step``
    registers into the bucket that also serves ``Given``, ``When`` and ``Then``
    - which is exactly why the port uses it (AAP deviation 7) - and the price of
    that reach is that two patterns can overlap on a concrete phrase without
    either being a duplicate registration behave would have rejected.  This
    function surfaces that: more than one result is a defect in the step text,
    not a tolerable outcome.

    All four buckets are scanned, not just ``step``, so that a definition
    accidentally bound to a keyword is *found and reported* through
    :attr:`StepMatch.bucket` rather than silently missed.

    :param phrase: A Gherkin step phrase, without its keyword.
    :returns: One :class:`StepMatch` per matching definition, in bucket order.
    :raises AssertionError: If a matching definition's type converter fails on
        this phrase.  behave models that outcome as a ``MatchWithError``, which
        carries the exception in ``stored_error`` and re-raises it only when the
        step is *run*; surfacing it here turns a deferred, confusing failure
        into an immediate, explained one.  This port does register a converter -
        ``features/steps/contacts_steps.py`` installs ``CukeStr`` - so the case
        is reachable rather than theoretical.
    """
    registry = load_step_registry()
    found: list[StepMatch] = []

    for bucket in STEP_BUCKETS:
        for matcher in registry.steps.get(bucket, ()):
            match = matcher.match(phrase)

            if not match:
                continue

            # Duck-typed rather than an ``isinstance`` against
            # ``behave.matchers.MatchWithError``: ``stored_error`` is the one
            # attribute that class adds and a successful ``Match`` never has
            # it, so this needs no import of behave's matcher classes and says
            # exactly what it means - a converter left an error behind.
            stored_error = getattr(match, "stored_error", None)

            if stored_error is not None:
                raise AssertionError(
                    f"step definition {matcher.pattern!r} at {matcher.location} "
                    f"matched {phrase!r} but its type converter failed: "
                    f"{stored_error!r}"
                )

            found.append(_build_step_match(phrase, matcher, match, bucket))

    return tuple(found)


def find_step_match(phrase: str) -> StepMatch | None:
    """The first step definition matching *phrase*, or ``None``.

    First-wins, which is behave's own ``find_match`` semantics.  Use it to ask
    whether a phrase resolves at all; use :func:`resolve_step` to reach a body,
    and :func:`find_all_step_matches` to prove a phrase is unambiguous.

    :param phrase: A Gherkin step phrase, without its keyword.
    :returns: The first match, or ``None`` when nothing matches.
    """
    matches = find_all_step_matches(phrase)
    return matches[0] if matches else None


def resolve_step(phrase: str) -> StepMatch:
    """The single step definition matching *phrase*.

    Keyword-agnostic by construction, and that is a requirement rather than a
    convenience: Cucumber-JVM matches a step by its text alone, so
    ``Session.java:12`` can declare the shared precondition ``@When`` while
    other features invoke it as a ``Given``.  The port reproduces that by
    registering every definition with ``@step``, and resolution here never
    consults a keyword.

    :param phrase: A Gherkin step phrase, without its keyword.
    :returns: The matching :class:`StepMatch`.
    :raises AssertionError: If nothing matches - an undefined step - or if more
        than one definition does.  Both messages name the phrase, and the
        ambiguous one lists every location, because that is what a fix needs.
    """
    matches = find_all_step_matches(phrase)

    if not matches:
        raise AssertionError(
            f"no step definition matches {phrase!r}. Every phrase in "
            f"features/*.feature must resolve to exactly one implementation "
            f"under {step_modules_dir()}"
        )

    if len(matches) > 1:
        locations = ", ".join(f"{match.pattern!r} at {match.location}" for match in matches)
        raise AssertionError(
            f"{len(matches)} step definitions match {phrase!r}: {locations}. "
            f"Ambiguous step text is a defect: @step reaches every keyword, so "
            f"two overlapping patterns can both match one phrase"
        )

    return matches[0]


@pytest.fixture(scope="session")
def step_registry() -> Any:
    """behave's step registry, with this port's step modules loaded once.

    Session-scoped, which is mandatory rather than an optimisation: the registry
    is process-wide state and a second load of a *changed* definition raises
    ``AmbiguousStep``.  One load per session also means the eleven step-related
    test modules share the registry the engine itself would build.

    :returns: The populated ``StepRegistry``.
    """
    return load_step_registry()


@pytest.fixture(name="resolve_step")
def _resolve_step_fixture(step_registry: Any) -> Callable[[str], StepMatch]:
    """:func:`resolve_step`, with the registry guaranteed loaded.

    The fixture form, so a parity module can declare its dependency on the
    registry in its signature instead of relying on import-time side effects::

        def test_login_navigates(resolve_step, fake_context, stub_driver):
            resolve_step("User is on the upgenix login page").run(fake_context)
            assert stub_driver.operations() == ("get",)

    :param step_registry: The loaded registry, requested so that loading happens
        during fixture setup.
    :returns: The :func:`resolve_step` function.
    """
    return resolve_step


# =========================================================================== #
# State isolation
#
# Two holders in this port are process-global by design, because the Java they
# port were a static initializer and a thread-local field.  pytest runs every
# test in one process, so both would otherwise carry a value from one test into
# the next:
#
#   * ``app/utils/properties.py``'s cache, which loads exactly once per process
#     and then never re-reads - the ``ConfigurationReader`` static-initializer
#     semantics of ``ConfigurationReader:11``;
#   * ``app/automation/driver.py``'s ``_holder``, the worker-local session slot
#     that ports ``Driver.java:17``'s ``InheritableThreadLocal``.
#
# The autouse fixture below returns both to their pre-test state around every
# test.  It touches exactly one private name in the whole application -
# ``driver._holder`` - and does so because that module's own docstring
# designates it the test seam: "tests/test_driver.py monkeypatches the holder to
# start from an empty slot; that seam is intentional".
# =========================================================================== #


@pytest.fixture(autouse=True)
def isolate_process_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Return both process-global holders to a clean state around every test.

    **The properties cache** is emptied through
    ``app.utils.properties.reset_cache()`` before and after each test.  That
    function is the module's declared test-support entry point: it is
    deliberately absent from ``properties.__all__`` and is *not* re-exported by
    ``app/utils/__init__.py``, which is why this module imports it from its
    defining module.  Resetting is what makes three otherwise impossible
    assertions possible in a single process - that the load happens once, that a
    missing file is logged exactly once, and that an initialized reader never
    re-reads.

    **The driver's session slot** is replaced with a brand new
    ``threading.local()``, so every test begins with an empty slot regardless of
    what its predecessor left behind, and ``monkeypatch`` restores the module's
    original holder at teardown.  ``app/automation/driver.py`` is imported
    *inside* this fixture rather than at module scope: it is the only module
    this suite reaches that imports selenium and webdriver-manager, and a
    collection-time import problem there must not be able to break tests that
    have nothing to do with the browser.  When it cannot be imported there is no
    holder to isolate, and the properties half of the fixture still does its
    job.

    Nothing else is reset, and one omission is deliberate enough to state:
    ``app/config.py``'s installed userdata is left exactly as it is.  Clearing
    it for every test would impose a policy on the great majority of tests that
    never touch it, and that module already documents a per-call mechanism a
    test can use through the public API alone, keeping the choice visible where
    it was made::

        def test_browser_override(request):
            from app import config

            request.addfinalizer(lambda: config.set_userdata(None))
            config.set_userdata({"browser": "firefox"})
            assert config.get_browser() == "firefox"

    ``set_userdata`` snapshots the mapping rather than aliasing it, and
    ``app/config.py`` documents ``None`` or an empty mapping as clearing the
    slot and restoring the file-only path, so the finalizer above is the whole
    of the cleanup required.  Registering it *before* installing means it runs
    even if the assertion fails.

    :param monkeypatch: pytest's patcher, used for its guaranteed teardown.
    :yields: ``None`` - the fixture is entirely about the state around the test.
    """
    properties.reset_cache()

    try:
        from app.automation import driver as driver_module
    except ImportError:  # pragma: no cover - only when selenium is unavailable
        driver_module = None

    if driver_module is not None:
        # A fresh holder rather than a cleared one: a new ``threading.local()``
        # carries no attributes at all, which is the state ``_session()``
        # reports as ``None`` and therefore the exact equivalent of the ``null``
        # ``Driver.java:22`` tests against.
        monkeypatch.setattr(driver_module, "_holder", threading.local())

    try:
        yield
    finally:
        # After as well as before.  A test that populated the cache should not
        # leave it populated for a later test that never asked for a reset, and
        # doing it in ``finally`` means a failing test cleans up too.
        properties.reset_cache()


# =========================================================================== #
# Determinism
#
# The rule this section exists to enforce is stated at the top of the module and
# restated once here because it is the easiest rule in the suite to break by
# accident: **compare structure, never bytes**.  A whole-artifact equality
# assertion against a generated report is guaranteed to fail on the second run,
# and the reasons are inherent rather than fixable - AAP 0.6 lists them: a
# per-scenario ``start_timestamp``, measured durations, tracebacks in failure
# text, timing surfaced in both HTML outputs, and screenshot bytes that differ
# from capture to capture.
#
# :func:`normalize_volatile` canonicalises exactly those fields and nothing
# else, so what survives it is structure: feature order, scenario order,
# Background position, the presence or absence of each key, and every value that
# a run does not get to choose.
# =========================================================================== #

#: The key whose value is a measured duration.  Nanosecond integers in the
#: Cucumber-JVM contract, float seconds in the engine's own output - both
#: volatile, so both are canonicalised.
DURATION_KEY: Final[str] = "duration"

#: The key carrying failure text: a Python assertion message plus a traceback
#: (AAP deviation 16), so it varies with the interpreter and the run.
ERROR_MESSAGE_KEY: Final[str] = "error_message"

#: The key carrying an embedding's base64 payload, canonicalised only when its
#: mapping also carries ``mime_type`` - see :func:`normalize_volatile`.
EMBEDDED_DATA_KEY: Final[str] = "data"

#: The companion key that identifies a mapping as an embedding.
MIME_TYPE_KEY: Final[str] = "mime_type"


def normalize_volatile(document: Any) -> Any:
    """Canonicalise every run-dependent value in a parsed result document.

    Applied to both sides of a comparison, this reduces an assertion to the
    question worth asking - is the *structure* the same - while leaving every
    structural fact intact.  Four kinds of value are replaced:

    ============================================  ==============================
    Key                                           Replaced with
    ============================================  ==============================
    ``start_timestamp``, ``started_at``,          :data:`TIMESTAMP_PLACEHOLDER`
    ``generated_at``
    ``duration``                                  :data:`DURATION_PLACEHOLDER`
    ``error_message``                             :data:`ERROR_MESSAGE_PLACEHOLDER`
    ``data``, inside an embedding                 :data:`EMBEDDED_DATA_PLACEHOLDER`
    ============================================  ==============================

    Two properties of the replacement matter as much as the choice of fields.
    **Presence is preserved**: a key that was there is still there, carrying a
    placeholder, so the JVM contract's distinction between a passed step
    (``{"duration": ..., "status": "passed"}``) and a skipped one
    (``{"status": "skipped"}``, with *no* duration key at all) survives
    normalization and stays assertable.  And **the input is never mutated**: a
    new document is returned, so a test may normalize a fixture and still
    compare against the original in the same breath.

    ``data`` is canonicalised only where its mapping also holds
    :data:`MIME_TYPE_KEY`, which is what an embedding looks like.  A key called
    ``data`` anywhere else in a document is left exactly as it is, because it is
    not a screenshot payload and blanketing every ``data`` key would erase real
    content.

    What is deliberately *not* touched: ``match.location``, which carries a
    dotted Python path (AAP deviation 8) and is fully determined by the code;
    ``line`` and ``uri``, which come from the feature files; ``status``, which is
    the outcome under test; and ``metadata``, whose interpreter and platform
    values are stable for a given environment.

    :param document: A parsed result document, or any nested structure of
        mappings, sequences and scalars.
    :returns: A new structure with the volatile values canonicalised.
    """
    if isinstance(document, Mapping):
        is_embedding = MIME_TYPE_KEY in document
        normalized: dict[Any, Any] = {}

        for key, value in document.items():
            if key in TIMESTAMP_KEYS:
                normalized[key] = TIMESTAMP_PLACEHOLDER
            elif key == DURATION_KEY:
                normalized[key] = DURATION_PLACEHOLDER
            elif key == ERROR_MESSAGE_KEY:
                normalized[key] = ERROR_MESSAGE_PLACEHOLDER
            elif key == EMBEDDED_DATA_KEY and is_embedding:
                normalized[key] = EMBEDDED_DATA_PLACEHOLDER
            else:
                normalized[key] = normalize_volatile(value)

        return normalized

    if isinstance(document, (list, tuple)):
        return [normalize_volatile(item) for item in document]

    return document


@pytest.fixture(name="normalize_volatile")
def _normalize_volatile_fixture() -> Callable[[Any], Any]:
    """:func:`normalize_volatile`, in fixture form.

    Both forms are supported so that a test module may either import the
    function - ``from conftest import normalize_volatile`` - or request it,
    whichever reads better where it is used.  The fixture holds no state, so the
    two are interchangeable.

    :returns: The :func:`normalize_volatile` function.
    """
    return normalize_volatile
