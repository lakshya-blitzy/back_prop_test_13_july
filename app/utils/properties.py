"""Standard-library reader for the optional Java ``.properties`` runtime configuration.

Source construct
----------------
``[.gitignore:L3]`` -- the single line ``configuration.properties``. That line is the
whole runtime configuration contract the source Java/Maven project ever committed: the
file itself is git-ignored, so it is deliberately absent from every checkout, and only
``configuration.properties.example`` is tracked. This module is the Python side of that
contract. It reads the file when a deployment provides one and stays quiet when it does
not.

Absence is the normal case
--------------------------
Because ``[.gitignore:L3]`` ignores the file, a fresh clone, a CI run and a container
build all start life without it. Every failure mode -- missing file, unreadable file,
undecodable bytes, malformed content -- therefore degrades to an EMPTY mapping, is logged
at ``DEBUG`` severity and is never raised. Callers can rely on this module never
propagating an exception, which is what lets the configuration layer treat the file as one
optional rung of its precedence chain instead of a hard dependency.

Why ``configparser`` is enough (and why no dependency may be added for this)
---------------------------------------------------------------------------
Java ``.properties`` files are flat ``key=value`` documents with no section headers, while
:mod:`configparser` refuses any document whose first content line is not a section header
(``MissingSectionHeaderError``). Prepending a synthesized header **in memory** bridges the
two formats exactly, so no third-party parser (``javaproperties`` or similar) is needed --
and none may be introduced: the dependency manifests are fully pinned and a parity test
asserts their contents.

The file on disk is never created, written or modified here. The synthesized header exists
only inside the string handed to :meth:`configparser.ConfigParser.read_string`.

Choice of synthesized section name
----------------------------------
``[properties]`` is used rather than ``[DEFAULT]``. Both parse the same content, but the
non-magic name avoids two ``configparser`` surprises: with ``[DEFAULT]``,
:meth:`configparser.ConfigParser.sections` returns an empty list (``DEFAULT`` is magic and
never listed) and :meth:`configparser.ConfigParser.defaults` hands back the parser's *live*
internal dictionary, which a caller could mutate. Reading an ordinary section yields a
fresh dictionary and makes any stray section header inside the file visible so that it can
be reported. Keys declared under a literal ``[DEFAULT]`` header in the file are still
picked up, because ``configparser`` inherits defaults into every section.

The four constructor arguments, and why each is mandatory
--------------------------------------------------------
Each was established by executing the parser against realistic ``.properties`` content on
the pinned interpreter rather than by reading documentation:

``comment_prefixes=("#", "!")``
    Java accepts both ``#`` and ``!`` as comment markers. The ``configparser`` default is
    ``("#", ";")``, so a perfectly legal ``! comment`` line raises ``ParsingError``. ``;``
    is not a Java comment marker and is intentionally not accepted.
``interpolation=None``
    The default ``BasicInterpolation`` raises ``InterpolationSyntaxError`` when a value
    containing ``%`` is *retrieved*; parsing itself succeeds, which makes the default
    especially treacherous. A ``%`` is legal in a Java property value.
``strict=False``
    The default raises ``DuplicateOptionError`` on a repeated key. Java's
    ``Properties.load`` keeps the last occurrence, and ``strict=False`` reproduces that
    last-one-wins behaviour.
``optionxform = str``
    The default lower-cases every key, so ``baseUrl`` would silently become ``baseurl``.
    Java property keys are case-sensitive, so the identity transform is installed and
    lookups stay exact-match. No case-folding fallback is layered on top.

Known limitations, stated honestly
----------------------------------
* Space-separated pairs (``key value``), which Java permits, cannot be parsed by
  ``configparser`` -- not even by widening ``delimiters`` -- so a file relying on them
  degrades to an empty mapping. Both ``key=value`` and ``key:value`` work.
* Java backslash escapes (``\\=``, ``\\:``, ``\\uNNNN``) are not unescaped, and a bare key
  with no separator is a parse error rather than an empty value.
* An indented line continues the previous value, per ``configparser`` semantics.
* The synthesized header shifts line numbers in parse diagnostics by one.

Writing a complete Java ``.properties`` parser to close those gaps is deliberately out of
scope: this module exists precisely because the standard library already covers the subset
of the format the project actually uses.

Caching
-------
Results are cached per resolved path, so repeated lookups never re-read the disk, and the
cached mapping is handed out as a read-only view that cannot be mutated. Absence is cached
too. Call :func:`clear_cache` after changing a file on disk; tests and long-lived
processes need that hook, which is why it is public API rather than an implementation
detail.

The cache is process-local, which matters under a threaded WSGI server and under parallel
test workers: each process keeps its own copy, and concurrent readers are safe because the
worst case is that two threads parse the same file once each and store identical results.

Scope
-----
This module supplies exactly one rung of the configuration precedence chain (constructor
argument, then the process environment, then an environment file, then
``configuration.properties``, then a hard-coded default): the ``configuration.properties``
rung. It reads no environment variable, loads no environment file, defines no application
default, and knows nothing about what any key means. It is a generic reader; the
configuration layer above owns the whole precedence policy.

Values are returned exactly as ``configparser`` produced them -- no case folding, no
unquoting, no Unicode normalisation and no stripping beyond the parser's own -- so
byte-sensitive literals survive intact.

Usage (the three accessors below are imported from ``app.utils.properties``)::

    values = load_properties()                       # every key, or {} when absent
    browser = get_property("browser", "chrome")      # exact-match lookup with a default
    configured = has_property("base.url")            # absent vs. present-but-empty
"""

import configparser
import logging
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Final

__all__ = [
    "DEFAULT_PROPERTIES_FILENAME",
    "SYNTHETIC_SECTION_NAME",
    "cache_clear",
    "clear_cache",
    "default_properties_path",
    "get_property",
    "has_property",
    "load_properties",
]

# Structured logging only: the logger is obtained, never configured. Handler, level and
# format belong exclusively to app/logging_config.py.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Name of the optional runtime configuration file, carried over verbatim from
# `[.gitignore:L3]` (source literals are data, never renamed or modernised). Because that
# line git-ignores the file, a fresh checkout never contains it: absence is the common path.
DEFAULT_PROPERTIES_FILENAME: Final[str] = "configuration.properties"

# Header synthesized in memory so that `configparser` will accept a section-less Java
# `.properties` document. Deliberately not `DEFAULT`, whose magic semantics surprise
# readers (see the module docstring).
SYNTHETIC_SECTION_NAME: Final[str] = "properties"

# Java `.properties` accepts BOTH `#` and `!` as comment markers. `;` is not a Java marker
# and is therefore not accepted; the `configparser` default of ("#", ";") would raise
# ParsingError on a legal `!` comment line.
_COMMENT_PREFIXES: Final[tuple[str, str]] = ("#", "!")

# Repository root, derived from this module's own location: app/utils/properties.py ->
# parents[0] is app/utils, parents[1] is app, parents[2] is the repository root. Anchoring
# on __file__ instead of the process working directory keeps the default path stable no
# matter where the Flask application or pytest was launched from.
_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

# Shared read-only empty result, returned by every graceful-degradation path. One
# module-level instance avoids allocating a new proxy on the common absent-file path, and
# being read-only it can never be polluted by a caller between reads.
_EMPTY_PROPERTIES: Final[Mapping[str, str]] = MappingProxyType({})


def default_properties_path() -> Path:
    """Return the absolute path this module reads when no explicit path is supplied.

    The path is the repository-root ``configuration.properties`` named at
    ``[.gitignore:L3]``. It is resolved from this module's own location, so it does not
    depend on the process working directory, and the file it names is normally absent.

    Returns:
        The absolute path of the default (optional) properties file. Existence is not
        checked and is not implied.
    """
    return _PROJECT_ROOT / DEFAULT_PROPERTIES_FILENAME


def _resolve_path(path: str | Path | None) -> Path | None:
    """Normalize a caller-supplied path into an absolute, cache-friendly ``Path``.

    ``~`` is expanded so a deployment may point at a file in a home directory, and the
    result is made absolute so that two spellings of the same file share one cache entry.
    Resolution of a non-existent path is not an error: the file is optional.

    Args:
        path: An explicit path, or ``None`` to use :func:`default_properties_path`.

    Returns:
        The resolved absolute path, or ``None`` when the path cannot be resolved at all
        (an unusable path is treated exactly like an absent file).
    """
    candidate = default_properties_path() if path is None else Path(path)
    try:
        return candidate.expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        # RuntimeError: `~` cannot be expanded because no home directory is known.
        # OSError/ValueError: the path is unusable on this platform (e.g. NUL bytes).
        _LOGGER.debug(
            "Properties path %r cannot be resolved (%s: %s); treating it as absent",
            str(candidate),
            type(exc).__name__,
            exc,
        )
        return None


def _read_source_text(path: Path) -> str | None:
    """Read the properties file as text, or return ``None`` if it cannot be read.

    Args:
        path: Absolute path of the file to read.

    Returns:
        The file's decoded content, or ``None`` when the file is absent, unreadable or not
        valid UTF-8. No exception is propagated in any of those cases.
    """
    try:
        # Explicit UTF-8 on every read so values are encoding-stable across platforms and
        # byte-sensitive literals survive verbatim.
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # The overwhelmingly common case, and not a problem: DEBUG only, never a warning.
        _LOGGER.debug("Optional properties file %s is absent; no file-sourced values", path)
        return None
    except (OSError, UnicodeDecodeError) as exc:
        # OSError covers PermissionError, IsADirectoryError and every other I/O failure.
        _LOGGER.debug(
            "Optional properties file %s could not be read (%s: %s); continuing without it",
            path,
            type(exc).__name__,
            exc,
        )
        return None


def _parse_properties(text: str, origin: Path) -> Mapping[str, str]:
    """Parse Java ``.properties`` text into an immutable mapping.

    A section header is synthesized in memory and prepended to ``text`` so that
    :mod:`configparser` accepts the section-less document. The file on disk is untouched.

    Args:
        text: The full content of a Java ``.properties`` document.
        origin: Path the content came from, used only in diagnostic messages.

    Returns:
        A read-only mapping of every key found, or an empty mapping when the content
        cannot be parsed. Values are returned exactly as ``configparser`` produced them.
    """
    parser = configparser.ConfigParser(
        comment_prefixes=_COMMENT_PREFIXES,
        interpolation=None,
        strict=False,
    )
    # Java property keys are case-sensitive, so the key transform is replaced with the
    # identity function; `configparser` would otherwise lower-case every key. Replacing a
    # method on an instance is exactly what the standard library documents for this, and
    # exactly what a type checker flags, so the ignore is scoped to the two codes actually
    # emitted rather than relaxing the project's type-checking configuration.
    parser.optionxform = str  # type: ignore[method-assign,assignment]

    document = f"[{SYNTHETIC_SECTION_NAME}]\n{text}"
    try:
        parser.read_string(document, source=str(origin))
    except configparser.Error as exc:
        # Covers ParsingError (for example the space-separated pairs Java allows but
        # `configparser` cannot express), MissingSectionHeaderError, DuplicateSectionError
        # and every other parser error. Malformed content degrades; it never raises.
        _LOGGER.debug(
            "Optional properties file %s could not be parsed (%s: %s); continuing without it",
            origin,
            type(exc).__name__,
            exc,
        )
        return _EMPTY_PROPERTIES

    unexpected = [name for name in parser.sections() if name != SYNTHETIC_SECTION_NAME]
    if unexpected:
        # Java `.properties` has no sections, so a header in the file is a format mistake.
        # Its keys are unreachable through this reader; say so instead of failing silently.
        _LOGGER.debug(
            "Optional properties file %s declares %d unexpected section header(s) (%s); "
            "keys under them are ignored",
            origin,
            len(unexpected),
            ", ".join(unexpected),
        )

    values = dict(parser[SYNTHETIC_SECTION_NAME])
    # Counts only: values may hold credentials and must never reach the log.
    _LOGGER.debug("Loaded %d properties from %s", len(values), origin)
    return MappingProxyType(values)


@cache
def _load_properties_cached(resolved_path: Path) -> Mapping[str, str]:
    """Load and cache the properties at an already-resolved absolute path.

    The cache is keyed on the resolved path, and a missing file is cached just like a
    present one, so the common "no file at all" case costs one stat per path per process.

    Args:
        resolved_path: Absolute path produced by :func:`_resolve_path`.

    Returns:
        A read-only mapping, empty when the file is absent, unreadable or malformed.
    """
    text = _read_source_text(resolved_path)
    if text is None:
        return _EMPTY_PROPERTIES
    return _parse_properties(text, resolved_path)


def load_properties(path: str | Path | None = None) -> Mapping[str, str]:
    """Return every key/value pair of the optional properties file.

    This is the single point through which ``configuration.properties``
    ``[.gitignore:L3]`` is read; nothing else in the application opens that file.

    Args:
        path: Properties file to read. Defaults to :func:`default_properties_path`; tests
            and alternative deployments may point it anywhere.

    Returns:
        A read-only mapping of the file's contents, or an empty mapping when the file is
        absent, unreadable or malformed. Never raises, and the returned view cannot be
        mutated, so the cache is safe to share.
    """
    resolved = _resolve_path(path)
    if resolved is None:
        return _EMPTY_PROPERTIES
    return _load_properties_cached(resolved)


def get_property(
    key: str,
    default: str | None = None,
    *,
    path: str | Path | None = None,
) -> str | None:
    """Return one property value by exact key match.

    Lookup is case-sensitive, matching Java's ``Properties``. A key present with an empty
    value yields ``""`` rather than ``default``; use :func:`has_property` to tell an
    absent key from a present-but-empty one.

    Args:
        key: Property name, matched exactly as written in the file.
        default: Value to return when the key is not present. Defaults to ``None``.
        path: Properties file to read. Defaults to :func:`default_properties_path`.

    Returns:
        The configured value, or ``default`` when the key is absent.
    """
    return load_properties(path).get(key, default)


def has_property(key: str, *, path: str | Path | None = None) -> bool:
    """Report whether a property is present, regardless of its value.

    Args:
        key: Property name, matched exactly as written in the file.
        path: Properties file to read. Defaults to :func:`default_properties_path`.

    Returns:
        ``True`` when the key exists in the file, including when its value is empty.
    """
    return key in load_properties(path)


def clear_cache() -> None:
    """Discard every cached parse so the next read observes the files on disk again.

    Required whenever a properties file is created, changed or deleted while the process
    is running: tests that write a temporary file per case, and long-lived services that
    are asked to reload. Exposed as a named function so callers never have to reach into
    the caching implementation.
    """
    _load_properties_cached.cache_clear()


# Alias for callers that spell the hook the way `functools` does. Both names refer to the
# same function; neither is deprecated.
cache_clear = clear_cache
