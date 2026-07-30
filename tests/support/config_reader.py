"""Harness-side cached access to the optional ``configuration.properties`` runtime file.

Source construct
================
``[.gitignore:L3]`` -- the single line ``configuration.properties``. That one line is the
entire runtime configuration contract the source Java/Maven project ever committed, and it
is a git-ignore rule rather than a file: the project declared that a deployment MAY drop a
properties file next to the build, and that the file must never be tracked. Only
``configuration.properties.example`` is committed, as the documented template.

This module is the harness-facing side of that contract, realising the migration's Cached
Configuration Reader pattern for everything under ``tests/``: the page objects, the driver
factory, the step definitions and the unit, integration and parity suites all reach the
properties file through here rather than opening it themselves.

A facade, not a parser
======================
``app/utils/properties.py`` is the SINGLE owner of the Java ``.properties`` parsing logic.
This module parses nothing, opens no file, and defines no parser: every read is delegated
to that module and the result is passed straight through. That is the migration's Rule T2,
"One source construct, one target module", applied literally -- ``[.gitignore:L3]`` is one
source construct, so exactly one module may know how to read it. Duplicating the parser
here would create two implementations that could silently disagree about the very file
whose contents this project's behaviour depends on.

Why the facade earns its place (it is not a pointless re-export)
===============================================================
It adds three things the application-side module cannot provide, and nothing else:

1. **Import safety.** ``import app.utils.properties`` first executes the parent package
   ``app/__init__.py``, which is the Flask application factory, so ANY ``app.*`` import
   transitively needs Flask installed. The harness must stay importable when it is not:
   a verification environment is not guaranteed to have network access, and the parity
   suite is deliberately written to need nothing beyond the standard library. The import
   here is therefore guarded, and an unavailable backend degrades to an empty mapping
   instead of breaking collection for every test in the package.
2. **Typed accessors.** The committed template carries integer settings (wait and timeout
   seconds, the six report thresholds) and boolean settings (``ignore.test.failures``,
   ``headless``, ``screenshots.enabled``, ``error.shots.enabled``). Java's ``Properties``
   hands back strings only, so the harness needs one audited place to turn those strings
   into ``bool`` and ``int`` rather than a coercion re-invented per call site.
3. **One harness-side entry point.** Test code imports from ``tests.support.config_reader``
   and never from ``app.*``, which keeps the guard in one place and keeps the dependency
   edge visible.

Absence is the common path, not the edge case
=============================================
Because ``[.gitignore:L3]`` ignores the file, a fresh clone, a CI run and a container build
all start life without it. "Not present" is therefore the DEFAULT state of this module, not
an exceptional one, and the API is shaped so that the absent case is the quiet, boring
path: every failure mode -- missing file, unreadable file, a directory where a file was
expected, undecodable bytes, malformed content, and an unavailable backend -- yields an
EMPTY mapping, is logged at ``DEBUG`` severity at most, and is never raised. Callers can
rely on nothing in this module ever propagating an exception and never calling ``sys.exit``,
which is what lets the configuration layer above treat the file as one optional rung of its
precedence chain rather than a hard dependency. Anything louder than ``DEBUG`` would fire
on every run of a healthy checkout, so warning and error severities are deliberately unused.

This module is read-only on the filesystem. It never creates, writes or modifies
``configuration.properties``; the real file stays git-ignored and only the ``.example``
template is committed.

Delegated behaviour this facade must never undo
===============================================
``app/utils/properties.py`` drives :mod:`configparser` with four non-default constructor
arguments, each of which was established by executing the parser rather than by reading
documentation. They are recorded here because this facade is the layer most likely to
"helpfully" undo one of them:

``comment_prefixes=("#", "!")``
    Java accepts both ``#`` and ``!`` as comment markers, while the ``configparser`` default
    is ``("#", ";")`` -- so a perfectly legal ``! comment`` line raises ``ParsingError``
    with the default. This is a live crash, not a theoretical one.
``interpolation=None``
    The default ``BasicInterpolation`` raises ``InterpolationSyntaxError`` for a value
    containing ``%``, which is legal in a Java property value.
``optionxform = str``
    The default lower-cases every key, but Java property keys are case-SENSITIVE, so
    ``baseUrl`` must not become ``baseurl``.
``strict=False``
    The default raises ``DuplicateOptionError`` on a repeated key, whereas Java's
    ``Properties.load`` keeps the last occurrence.

Concretely, this module therefore does NOT lower-case keys, does NOT add a case-folding
lookup fallback, does NOT re-apply interpolation, and does NOT de-duplicate or reorder
entries. Key lookup is exact-match. Values are handed back exactly as ``configparser``
produced them: no stripping beyond the parser's own, no unquoting, no case folding and no
Unicode normalisation.

Known limitation, stated honestly
=================================
Space-separated pairs (``browser chrome``), which Java permits, cannot be parsed by
``configparser`` -- not even by widening its ``delimiters`` -- so a file that relies on them
degrades to an empty mapping rather than parsing partially. Both ``key=value`` and
``key:value`` work, as does surrounding whitespace around the separator. Java backslash
escapes are not unescaped. Writing a complete Java ``.properties`` parser to close those
gaps is deliberately out of scope: it would duplicate the module this one delegates to and
contradict the migration's fully-pinned, no-new-dependency mandate.

Caching, and the double-cache hazard avoided by construction
============================================================
Caching is delegated ENTIRELY to ``app/utils/properties.py``, which caches per resolved
path and caches absence too. This module adds NO second cache layer, and that is a
deliberate correctness decision rather than an omission.

Two independent caches with two independent clear hooks is the subtlest bug this file could
carry: a test that wrote a temporary file, read it, rewrote it with different content and
read again would keep observing the stale value if only one of the two caches had been
cleared, and the test written to catch that is the very test that depends on the clear hook
working. With a single cache there is nothing to get out of step.

:func:`clear_configuration_cache` is still public API, so consumers have one stable name to
call after changing a file on disk, and it forwards to the backend's own hook. Because the
delegated cache is process-local, each ``pytest-xdist`` worker keeps its own copy, which is
what makes it safe under the ``-n logical`` parallelism this project runs by default.

Precedence: this module is rung four of five
============================================
The migration's configuration precedence is, in order: an explicit constructor argument,
then the process environment, then an environment file, then ``configuration.properties``,
then a hard-coded default. This module supplies the ``configuration.properties`` rung and
nothing else. It reads no environment variable, loads no environment file, and declares no
application default -- the clone URL, the tag expression, the failure-tolerance flag, the
six report thresholds, the report sorting method, the report include pattern and the
artifact root are all owned elsewhere, and re-declaring any of them here would create a
competing source of truth. This is a generic Java ``.properties`` accessor: it knows how to
read keys and nothing about what any key means. No credential, token, password or
application URL is hard-coded here, and none may be.

Preserved defect D5
===================
The committed template carries ``expected.empty.field.message=Veuillez renseigner ce
champ.``. That French string is the assertion the Gherkin actually makes at
``[README.md:L135]``, while the explanatory comment above it at ``[README.md:L130]``
describes the English "Please fill out this field". The two disagree, and the assertion is
the executable truth; the mismatch is catalogued as defect D5 and is preserved
INTENTIONALLY, per the migration's Rule T4, "Defects are behavior". Fixing it is explicitly
out of scope. ``docs/migration-parity.md`` is the authoritative D1-D9 register.

For this module the practical consequence is a hard requirement: the value must round-trip
byte-exactly, all 29 pure-ASCII characters including the trailing period. It must never be
translated to match the English comment and never stripped, casefolded or normalised. That
is why no accessor here transforms a string value, and why the boolean and integer
accessors are documented as reading a value without ever rewriting it.

Usage
=====
::

    from tests.support import config_reader

    values = config_reader.load_properties()                    # every key, or {} when absent
    browser = config_reader.get_property("browser", "chrome")    # exact-match, with a default
    headless = config_reader.get_boolean_property("headless", True)
    seconds = config_reader.get_int_property("explicit.wait.seconds", 30)
    config_reader.clear_configuration_cache()                   # after changing the file
"""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

# Structured logging only: the logger is obtained here, never configured. Handler, level and
# format belong exclusively to app/logging_config.py.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

__all__ = [
    "BOOLEAN_FALSE_LITERALS",
    "BOOLEAN_TRUE_LITERALS",
    "PROPERTIES_BACKEND_MODULE",
    "clear_configuration_cache",
    "configuration_properties_path",
    "get_boolean_property",
    "get_int_property",
    "get_property",
    "has_property",
    "is_backend_available",
    "load_properties",
]

# Dotted name of the module every read is delegated to. Named as data so a diagnostic
# message and a test can both refer to the delegation target without hard-coding it twice.
PROPERTIES_BACKEND_MODULE: Final[str] = "app.utils.properties"

# The literals :func:`get_boolean_property` recognises, published so the accepted vocabulary
# is machine-checkable rather than only described in prose. Matching is case-insensitive on
# a throwaway copy of the value; the stored value itself is never rewritten. Java's
# `Boolean.parseBoolean` treats every non-"true" string as false, which silently turns a
# typo into a false; this reader instead recognises a closed set and falls back to the
# caller's own default for anything outside it, so a typo cannot masquerade as a decision.
# The source literal at `[pom.xml:L25]`, <testFailureIgnore>true</testFailureIgnore>, and
# every boolean in the committed template are the lowercase spellings below.
BOOLEAN_TRUE_LITERALS: Final[frozenset[str]] = frozenset({"true", "yes", "on", "1"})
BOOLEAN_FALSE_LITERALS: Final[frozenset[str]] = frozenset({"false", "no", "off", "0"})

# Shared read-only empty result handed back by every degradation path. One module-level
# instance avoids allocating on the common absent-file path, and being read-only it can
# never be polluted by a caller between reads.
_EMPTY_PROPERTIES: Final[Mapping[str, str]] = MappingProxyType({})


class _PropertiesBackend(Protocol):
    """The structural contract this facade needs from :data:`PROPERTIES_BACKEND_MODULE`.

    Only the three members actually used are declared, so the coupling between the harness
    and the application-side reader is stated explicitly and stays as narrow as possible.
    Declaring it as a protocol also means a type checker verifies every delegated call site
    against these signatures instead of treating the imported module as untyped.
    """

    def load_properties(self, path: str | Path | None = ...) -> Mapping[str, str]:
        """Return every key/value pair of the properties file, or an empty mapping."""
        ...

    def clear_cache(self) -> None:
        """Discard every cached parse so the next read observes the file on disk again."""
        ...

    def default_properties_path(self) -> Path:
        """Return the absolute path read when no explicit path is supplied."""
        ...


# The guarded delegation import. `ImportError` is caught deliberately NARROWLY: it covers
# the one failure this module is designed to survive -- the `app` package, or Flask beneath
# it, not being importable -- while any other exception raised by the application package is
# a genuine defect there and must stay loud rather than being silently downgraded to "no
# configuration". `ModuleNotFoundError` is a subclass of `ImportError`, so a missing Flask
# and a missing `app` are both covered.
_BACKEND: _PropertiesBackend | None
try:
    from app.utils import properties as _properties_module
except ImportError as exc:  # pragma: no cover - depends on the installed environment
    _BACKEND = None
    _LOGGER.debug(
        "Properties backend %s is unavailable (%s: %s); "
        "file-sourced configuration will be empty",
        PROPERTIES_BACKEND_MODULE,
        type(exc).__name__,
        exc,
    )
else:
    # `cast` carries no runtime cost and performs no runtime check: it tells the type checker
    # that this module satisfies the protocol above, which is how a module object is given a
    # precise static type.
    _BACKEND = cast(_PropertiesBackend, _properties_module)


def is_backend_available() -> bool:
    """Report whether the delegated properties reader could be imported.

    Purely diagnostic: it distinguishes "the mapping is empty because no properties file is
    present", which is the normal state of a fresh checkout, from "the mapping is empty
    because :data:`PROPERTIES_BACKEND_MODULE` could not be imported at all". Both cases
    behave identically -- an empty mapping and no exception -- so no caller needs to consult
    this in order to be correct.

    Returns:
        ``True`` when reads are delegated to :data:`PROPERTIES_BACKEND_MODULE`, ``False``
        when every read degrades to an empty mapping.
    """
    return _BACKEND is not None


def configuration_properties_path() -> Path | None:
    """Return the absolute path consulted when no explicit path is supplied.

    The path names the repository-root ``configuration.properties`` of ``[.gitignore:L3]``.
    Existence is neither checked nor implied: that file is git-ignored, so it is normally
    absent.

    Returns:
        The absolute default path, or ``None`` when the delegated reader is unavailable and
        therefore no path is consulted at all.
    """
    if _BACKEND is None:
        return None
    return _BACKEND.default_properties_path()


def load_properties(path: str | Path | None = None) -> Mapping[str, str]:
    """Return every key/value pair of the optional properties file.

    This is the single point through which the harness reads ``configuration.properties``
    ``[.gitignore:L3]``. Parsing, path resolution and caching all happen in
    :data:`PROPERTIES_BACKEND_MODULE`; this function only guards the delegation and
    guarantees a read-only result.

    Args:
        path: Properties file to read. Defaults to :func:`configuration_properties_path`;
            tests point it at a temporary file and alternative deployments may point it
            anywhere.

    Returns:
        A read-only mapping of the file's contents, or an empty mapping when the file is
        absent, unreadable or malformed, or when the delegated reader is unavailable. Keys
        keep the case they were written with, values are byte-for-byte as parsed, and the
        result cannot be mutated, so the shared cache behind it stays trustworthy. Never
        raises.
    """
    if _BACKEND is None:
        return _EMPTY_PROPERTIES
    # Re-wrapped rather than returned as-is: the delegated reader already hands back a
    # read-only view, and wrapping is an O(1) view over the same data, so this costs nothing
    # and keeps the immutability guarantee of this API true independently of what the
    # backend chooses to return.
    return MappingProxyType(_BACKEND.load_properties(path))


def get_property(
    key: str,
    default: str | None = None,
    *,
    path: str | Path | None = None,
) -> str | None:
    """Return one property value by exact key match, without transforming it.

    Lookup is case-sensitive, matching Java's ``Properties``. A key that is present with an
    empty value yields ``""`` rather than ``default``; use :func:`has_property` to tell an
    absent key from a present-but-empty one.

    The value is returned exactly as parsed -- not stripped, unquoted, casefolded or
    normalised -- which is what lets byte-sensitive literals survive. The preserved defect
    D5 string ``Veuillez renseigner ce champ.`` is the concrete case that depends on it:
    all 29 ASCII characters, trailing period included, must come back intact.

    Args:
        key: Property name, matched exactly as written in the file.
        default: Value to return when the key is absent. Defaults to ``None``.
        path: Properties file to read. Defaults to :func:`configuration_properties_path`.

    Returns:
        The configured value verbatim, or ``default`` when the key is absent.
    """
    return load_properties(path).get(key, default)


def has_property(key: str, *, path: str | Path | None = None) -> bool:
    """Report whether a property is present, whatever its value.

    Needed because a present-but-empty value (``key=``) is a real configuration statement
    and is indistinguishable from an absent key through :func:`get_property` alone whenever
    the caller's default is also empty.

    Args:
        key: Property name, matched exactly as written in the file.
        path: Properties file to read. Defaults to :func:`configuration_properties_path`.

    Returns:
        ``True`` when the key exists, including when its value is the empty string.
    """
    return key in load_properties(path)


def get_boolean_property(
    key: str,
    default: bool = False,
    *,
    path: str | Path | None = None,
) -> bool:
    """Return one property interpreted as a boolean.

    The value is matched case-insensitively against :data:`BOOLEAN_TRUE_LITERALS` and
    :data:`BOOLEAN_FALSE_LITERALS`. Matching uses a lower-cased, whitespace-stripped
    throwaway copy; the stored value is never rewritten, and :func:`get_property` keeps
    returning it verbatim. Anything outside those two sets -- including an empty value and
    a misspelling such as ``ture`` -- yields ``default`` and is logged at ``DEBUG``, so an
    unreadable setting falls back to the caller's documented default instead of being
    silently read as false.

    Args:
        key: Property name, matched exactly as written in the file.
        default: Value to return when the key is absent or its value is unrecognised.
        path: Properties file to read. Defaults to :func:`configuration_properties_path`.

    Returns:
        The configured boolean, or ``default``.
    """
    raw = get_property(key, path=path)
    if raw is None:
        return default

    candidate = raw.strip().lower()
    if candidate in BOOLEAN_TRUE_LITERALS:
        return True
    if candidate in BOOLEAN_FALSE_LITERALS:
        return False

    # Key name and length only: a value may hold a credential and must never reach the log.
    _LOGGER.debug(
        "Property %r holds a %d-character value that is not a recognised boolean; "
        "using the caller's default %r",
        key,
        len(raw),
        default,
    )
    return default


def get_int_property(
    key: str,
    default: int = 0,
    *,
    path: str | Path | None = None,
) -> int:
    """Return one property interpreted as an integer.

    Parsed with the standard library's :class:`int`, so an optional sign and surrounding
    whitespace are accepted -- ``-1``, the value the six report thresholds carry, parses
    correctly -- as is Python's underscore digit grouping. A value that is not a valid
    integer, such as an empty value or ``30.0``, yields ``default`` and is logged at
    ``DEBUG`` rather than raising. The stored string is never rewritten.

    Args:
        key: Property name, matched exactly as written in the file.
        default: Value to return when the key is absent or its value is not an integer.
        path: Properties file to read. Defaults to :func:`configuration_properties_path`.

    Returns:
        The configured integer, or ``default``.
    """
    raw = get_property(key, path=path)
    if raw is None:
        return default

    try:
        return int(raw)
    except ValueError:
        # Key name and length only, for the same reason as in `get_boolean_property`.
        _LOGGER.debug(
            "Property %r holds a %d-character value that is not a valid integer; "
            "using the caller's default %d",
            key,
            len(raw),
            default,
        )
        return default


def clear_configuration_cache() -> None:
    """Discard every cached parse so the next read observes the files on disk again.

    Required whenever a properties file is created, changed or deleted while the process is
    running: a unit test that writes one temporary file per case, or a long-lived service
    asked to reload. Because this module keeps no cache of its own, the call forwards to the
    single cache that exists, in :data:`PROPERTIES_BACKEND_MODULE` -- there is no second
    layer that could be left holding stale values, which is precisely why no second layer
    was introduced.

    The forward is guarded: with the delegated reader unavailable there is no cache to
    clear, so the call is a no-op rather than an error. Safe to call at any time, including
    before the first read.
    """
    if _BACKEND is None:
        _LOGGER.debug(
            "Properties backend %s is unavailable; there is no cache to clear",
            PROPERTIES_BACKEND_MODULE,
        )
        return
    _BACKEND.clear_cache()
