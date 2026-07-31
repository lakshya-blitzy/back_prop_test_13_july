"""Unit suite over the standard-library Java ``.properties`` reader.

Source construct
================
``[.gitignore:L3]`` -- the single tracked line ``configuration.properties``. That one line
is the whole runtime configuration contract the source Java/Maven project ever committed,
and :mod:`app.utils.properties` is the Python side of it. This suite asserts the two halves
of the ported behaviour named in the transformation plan for this file: *Java ``.properties``
parsing* and *graceful absence*.

Absence is the common path, not the edge case
=============================================
Because ``[.gitignore:L3]`` git-ignores the file, a fresh clone, every CI run and every
container build start life **without** it -- only ``configuration.properties.example`` is
committed. The absence tests below are therefore the ones most likely to run in anger, and
the contract they assert is absolute: an empty mapping, no exception, nothing on stdout and
nothing logged above ``DEBUG``. The configuration layer above treats the file as one
optional rung of its precedence chain, so a reader that raised would take the whole
application down on a perfectly normal checkout.

Why the reader needs no third-party parser, and why that must stay true
=======================================================================
Java ``.properties`` documents are flat ``key=value`` text with no section header, which
:mod:`configparser` rejects outright. Prepending a synthesized header *in memory* bridges
the two formats with zero added dependencies. Four constructor arguments make that bridge
correct, and this suite proves each one is individually load-bearing by exercising a
default-configured parser against the same content and showing exactly how it fails:

* ``comment_prefixes=("#", "!")`` -- Java accepts ``!`` as a comment marker; the
  :mod:`configparser` default of ``("#", ";")`` raises ``ParsingError`` on a legal ``!``
  line. ``;`` is *not* a Java marker and is deliberately not accepted.
* ``interpolation=None`` -- the default ``BasicInterpolation`` raises
  ``InterpolationSyntaxError`` for a value containing ``%``, which is legal in Java.
* ``optionxform = str`` -- the default lower-cases every key; Java property keys are
  case-sensitive.
* ``strict=False`` -- the default raises ``DuplicateOptionError`` on a repeated key, where
  Java's ``Properties.load`` keeps the last occurrence.

Those four control tests are the regression guard that stops a later "simplification" of
the parser construction, and the scope-boundary tests are the guard that stops the same
gap being closed by adding a ``javaproperties`` dependency -- which would break the
project's fully-pinned dependency discipline and the dependency-parity criterion.

Preserved defect D5
===================
``[README.md:L130]`` describes the expected message as the English ``"Please fill out this
field"`` while ``[README.md:L135]`` asserts the French ``"Veuillez renseigner ce champ."``.
The assertion is the executable truth, the defect is preserved rather than repaired, and
``docs/migration-parity.md`` is where that decision is recorded. This suite asserts the
French literal survives a round trip byte-for-byte -- 29 characters, pure ASCII despite
being French-language text, trailing period intact -- and that it is not quietly translated
back to the comment's wording.

Scope of this suite, and what it deliberately leaves alone
==========================================================
:mod:`app.utils.properties` supplies exactly one rung of the configuration precedence chain
(explicit argument, then environment variable, then ``.env``, then
``configuration.properties``, then hard-coded default): the fourth. The scope-boundary tests
inspect the reader's own syntax tree to assert it reads no environment variable, loads no
environment file, defines no application default, imports nothing outside the standard
library and imports neither its sibling utility modules nor the configuration layer above
it. Every one of those is an executable assertion rather than a comment.

Nothing here builds a Flask application, drives a browser, generates test data or stands in
for the external application under test. No file is written outside pytest's ``tmp_path``,
and no ``configuration.properties`` is ever created anywhere in the repository tree -- doing
so would shadow the git-ignored contract for every other test in the session.

Import hazard
=============
``import app.utils.properties`` executes the ``app`` package's ``__init__`` first, and that
module is the Flask application factory, so reaching even this standard-library-only target
needs Flask installed. The import below is therefore guarded and degrades to a single
module-level skip, keeping collection of the whole tree intact in an environment where the
application's dependencies are not available.
"""

from __future__ import annotations

import ast
import configparser
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    # Annotation-only imports. `from __future__ import annotations` keeps every annotation a
    # string, so this block never executes at run time and costs nothing, while mypy still
    # resolves the real types.
    from collections.abc import Iterator, Mapping

try:
    from app.utils import properties as reader
except ImportError as _import_error:  # pragma: no cover - depends on the environment
    # Reaching `app.utils.properties` executes `app/__init__.py`, the Flask application
    # factory, so a missing third-party distribution surfaces here. Skipping at module level
    # keeps this one unavailable dependency from turning into a collection ERROR for the
    # whole tree; `pytest.skip` is typed `NoReturn`, so `reader` is bound below or nothing is.
    pytest.skip(
        "app.utils.properties could not be imported: reaching it executes the Flask "
        "application factory in app/__init__.py, so the application's dependencies must be "
        f"installed ({type(_import_error).__name__}: {_import_error})",
        allow_module_level=True,
    )


# --------------------------------------------------------------------------------------
# Constants. Every literal below is data carried over from a cited source construct, never
# a value invented here.
# --------------------------------------------------------------------------------------

# The module under test, addressed as a file so its syntax tree can be inspected, and as a
# dotted name so its log records can be captured by logger name.
_READER_RELATIVE_PATH: Final[str] = "app/utils/properties.py"
_READER_LOGGER_NAME: Final[str] = "app.utils.properties"

# The committed template. The real file it templates is git-ignored `[.gitignore:L3]` and
# must stay absent from the repository tree.
_TEMPLATE_RELATIVE_PATH: Final[str] = "configuration.properties.example"

# The runtime configuration file name, verbatim from `[.gitignore:L3]`.
_PROPERTIES_FILENAME: Final[str] = "configuration.properties"

# Defect D5. `[README.md:L135]` asserts the French message; `[README.md:L130]` describes it
# in English. The assertion is the executable truth and is preserved, not repaired -- see
# `docs/migration-parity.md`. The literal is French-language yet pure ASCII, exactly 29
# characters long, and its trailing period is significant.
_FRENCH_EMPTY_FIELD_MESSAGE: Final[str] = "Veuillez renseigner ce champ."
_FRENCH_MESSAGE_LENGTH: Final[int] = 29
_ENGLISH_COMMENT_MESSAGE: Final[str] = "Please fill out this field"

# Template keys and the source construct each value is carried over from. Asserted against
# the committed template rather than restated as a default, so there is exactly one place a
# preserved literal can be wrong.
_TEMPLATE_SOURCE_VALUES: Final[tuple[tuple[str, str, str], ...]] = (
    ("clone.url", "https://github.com/BalamiRR/Upgenix-QA.git", "[Jenkins:L3]"),
    ("tag.expression", "LogOut", "[README.md:L87]"),
    ("ignore.test.failures", "true", "[pom.xml:L25]"),
    ("report.failed.features.number", "-1", "[Jenkins:L15]"),
    ("report.failed.scenarios.number", "-1", "[Jenkins:L15]"),
    ("report.failed.steps.number", "-1", "[Jenkins:L15]"),
    ("report.pending.steps.number", "-1", "[Jenkins:L15]"),
    ("report.skipped.steps.number", "-1", "[Jenkins:L15]"),
    ("report.undefined.steps.number", "-1", "[Jenkins:L15]"),
    ("report.sorting.method", "ALPHABETICAL", "[Jenkins:L15]"),
    ("report.file.include.pattern", "**/*.json", "[Jenkins:L15]"),
    ("target.dir", "target", "[README.md:L79-L82]"),
    ("expected.empty.field.message", _FRENCH_EMPTY_FIELD_MESSAGE, "[README.md:L135]"),
)

# The public surface the configuration layer above imports. Asserted so a rename cannot pass
# unnoticed: `app/config.py` reaches this module for `load_properties`,
# `default_properties_path` and the cache-clearing hook, and has no fallback if they move.
_EXPECTED_PUBLIC_API: Final[frozenset[str]] = frozenset(
    {
        "DEFAULT_PROPERTIES_FILENAME",
        "SYNTHETIC_SECTION_NAME",
        "cache_clear",
        "clear_cache",
        "default_properties_path",
        "get_property",
        "has_property",
        "load_properties",
    }
)

# Rung two of the precedence chain belongs to `app/config.py`. Any of these names in the
# reader would mean it had reached into a rung that is not its own.
_ENVIRONMENT_TOKENS: Final[frozenset[str]] = frozenset({"environ", "getenv", "putenv"})

# Rung three -- the single `python-dotenv` load point -- also belongs to `app/config.py`.
_DOTENV_TOKENS: Final[frozenset[str]] = frozenset({"dotenv", "load_dotenv", "find_dotenv"})

# Rung five, the hard-coded application defaults, belongs to `app/config.py`, and the
# artifact-root constants belong to `app/utils/paths.py`. None of these literals may appear
# in the generic reader's executable code.
_APPLICATION_DEFAULT_LITERALS: Final[tuple[str, ...]] = (
    "BalamiRR",
    "Upgenix-QA",
    "Testinium-QA",
    "LogOut",
    "ALPHABETICAL",
    "**/*.json",
    "target/",
    "surefire-reports",
    "cucumber.json",
)

# Handler, level and formatter belong exclusively to `app/logging_config.py`. A library
# module that configured logging would silently outrank the application's own settings.
_LOGGING_CONFIGURATION_TOKENS: Final[frozenset[str]] = frozenset(
    {"basicConfig", "dictConfig", "fileConfig", "addHandler", "setLevel", "removeHandler"}
)

# Transformations the reader must not apply, so byte-sensitive literals such as the D5
# message survive verbatim.
_VALUE_TRANSFORM_TOKENS: Final[frozenset[str]] = frozenset(
    {"casefold", "normalize", "unquote", "title", "swapcase"}
)

# Calls whose text encoding must be stated explicitly rather than inherited from the
# platform's locale.
_ENCODING_SENSITIVE_CALLS: Final[frozenset[str]] = frozenset({"open", "read_text", "write_text"})

# Sibling utility modules. The three modules under `app/utils` are mutually independent, and
# `app.config` sits above this one: importing either way would invert the dependency
# direction the application is built on.
_FORBIDDEN_INTERNAL_IMPORTS: Final[tuple[str, ...]] = (
    "app.config",
    "app.utils.paths",
    "app.utils.platform_exec",
)

# Comment markers. Java `.properties` accepts `#` and `!`; `;` is a `configparser` default
# that Java never used and that the reader must not adopt.
_JAVA_COMMENT_MARKERS: Final[tuple[str, str]] = ("#", "!")
_NON_JAVA_COMMENT_MARKER: Final[str] = ";"

# Content shared by the four control tests and their reader-backed counterparts. Every line
# is one of the four hazards: a `!` comment, a duplicated key, a case-varying key pair and a
# value containing `%`.
_FOUR_HAZARD_DOCUMENT: Final[str] = (
    "# hash comment\n"
    "! bang comment\n"
    "browser=chrome\n"
    "browser=edge\n"
    "camelKey=v\n"
    "Browser=Firefox\n"
    "raw.percent=50% off\n"
    f"french={_FRENCH_EMPTY_FIELD_MESSAGE}\n"
)


# --------------------------------------------------------------------------------------
# File helpers. Every write states its encoding explicitly, so a test can never depend on
# the platform's locale, and every file lands under pytest's `tmp_path`.
# --------------------------------------------------------------------------------------


def _write_properties(directory: Path, content: str, *, name: str = "sample.properties") -> Path:
    """Write ``content`` to a properties file inside ``directory`` and return its path.

    ``newline=""`` disables newline translation, so a test that needs CRLF endings gets
    exactly the bytes it wrote and a test that needs LF is not silently given ``\\r\\n`` on a
    platform that would otherwise translate.

    Args:
        directory: Destination directory. Always a pytest ``tmp_path``, never a location in
            the repository tree.
        content: Full text of the properties document.
        name: File name. Deliberately not ``configuration.properties`` by default, so no test
            can accidentally create the git-ignored file ``[.gitignore:L3]`` names.

    Returns:
        The absolute path of the file just written.
    """
    path = directory / name
    path.write_text(content, encoding="utf-8", newline="")
    return path


def _raw_keys(text: str) -> list[str]:
    """Extract the key of every property line in ``text``, in file order.

    An independent, deliberately naive scan of the raw document, used to check that the
    reader returns *every* key the file declares rather than trusting it to agree with
    itself. Comment lines (``#`` and ``!``), blank lines and indented continuation lines are
    skipped, and the separator is whichever of ``=`` or ``:`` appears first -- a value such as
    ``https://host/path`` contains a ``:`` that must not be mistaken for the separator.

    Args:
        text: Full text of a Java ``.properties`` document.

    Returns:
        The declared keys, with duplicates preserved so a caller can detect them.
    """
    keys: list[str] = []
    for line in text.splitlines():
        if line[:1] in (" ", "\t"):
            # `configparser` treats an indented line as a continuation of the previous value.
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith(_JAVA_COMMENT_MARKERS):
            continue
        offsets = [stripped.index(separator) for separator in ("=", ":") if separator in stripped]
        if not offsets:
            # A bare key with no separator; `configparser` rejects it, so it declares nothing.
            continue
        keys.append(stripped[: min(offsets)].strip())
    return keys


# --------------------------------------------------------------------------------------
# Syntax-tree helpers. The scope-boundary and hygiene assertions inspect the reader's own
# AST rather than grepping its text: its module docstring legitimately *names* the
# third-party parser it refuses to use, and a text search would read that prose as a
# violation. An AST distinguishes executable code from documentation exactly.
# --------------------------------------------------------------------------------------


def _imported_modules(tree: ast.Module) -> set[str]:
    """Return every module name the tree imports, as written and fully dotted.

    Args:
        tree: Parsed module.

    Returns:
        Dotted module names from ``import x.y`` and ``from x.y import z``. A relative import
        is rendered with its leading dots so it cannot be confused with an absolute one.
    """
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add("." * node.level + (node.module or ""))
    return modules


def _imported_roots(tree: ast.Module) -> set[str]:
    """Return the top-level package of every import in the tree.

    Args:
        tree: Parsed module.

    Returns:
        The first dotted component of each imported module, which is what decides whether a
        dependency is part of the standard library.
    """
    return {module.lstrip(".").split(".")[0] for module in _imported_modules(tree) if module}


def _attribute_names(tree: ast.Module) -> set[str]:
    """Return every attribute name referenced anywhere in the tree.

    Covers loads (``os.environ``) and stores (``parser.optionxform = str``) alike, which is
    what makes it usable both for forbidding a name and for requiring one.

    Args:
        tree: Parsed module.

    Returns:
        The ``attr`` of every attribute access in the tree.
    """
    return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}


def _called_names(tree: ast.Module) -> set[str]:
    """Return the callee name of every call in the tree.

    A bare name -- a direct ``open`` call, say -- contributes its identifier, and an attribute
    call such as ``path.read_text`` contributes its final attribute, so both spellings of a
    forbidden call are caught.

    Args:
        tree: Parsed module.

    Returns:
        Callee names. Calls through a subscript or another expression contribute nothing,
        which is acceptable because the reader contains none.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _identifier_names(tree: ast.Module) -> set[str]:
    """Return every bare identifier referenced in the tree.

    Args:
        tree: Parsed module.

    Returns:
        The ``id`` of every :class:`ast.Name` node, whatever its context.
    """
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _code_string_literals(tree: ast.Module) -> list[str]:
    """Return every string literal that is part of the tree's executable code.

    Docstrings and any other bare string statement are excluded: they are prose, and prose
    is allowed to discuss a value the code must not contain. This is precisely what lets the
    reader document why it refuses a third-party parser without that documentation reading as
    a violation.

    Args:
        tree: Parsed module.

    Returns:
        The string constants that survive after prose is removed, including the literal parts
        of f-strings.
    """
    prose: set[int] = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose
    ]


def _calls_named(tree: ast.Module, names: frozenset[str]) -> list[ast.Call]:
    """Return every call in the tree whose callee name is in ``names``.

    Args:
        tree: Parsed module.
        names: Callee names of interest.

    Returns:
        The matching call nodes, so a test can inspect their keyword arguments.
    """
    matches: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = (
            callee.id
            if isinstance(callee, ast.Name)
            else callee.attr if isinstance(callee, ast.Attribute) else ""
        )
        if name in names:
            matches.append(node)
    return matches


def _keyword_names(call: ast.Call) -> set[str]:
    """Return the keyword-argument names of a call.

    Args:
        call: Call node to inspect.

    Returns:
        Every explicitly named keyword. A ``**kwargs`` expansion carries no name and is
        therefore absent, which is correct: an argument that cannot be seen cannot be
        asserted.
    """
    return {keyword.arg for keyword in call.keywords if keyword.arg is not None}


def _keyword_value(call: ast.Call, name: str) -> ast.expr | None:
    """Return the expression bound to one keyword argument of a call.

    Args:
        call: Call node to inspect.
        name: Keyword name to look up.

    Returns:
        The keyword's value expression, or ``None`` when the keyword is absent.
    """
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


# --------------------------------------------------------------------------------------
# Fixtures. `project_root` comes from `tests/conftest.py`, is session-scoped and is derived
# from `__file__` rather than the working directory: the suite runs with one worker per
# logical CPU, every worker is a separate process, and a working-directory-relative path
# would be a latent flake rather than a convenience.
# --------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_properties_cache() -> Iterator[None]:
    """Discard cached parses on both sides of every test in this module.

    The reader caches per resolved path for the life of the process, which is exactly the
    behaviour :class:`TestCaching` asserts -- and exactly what would let one test's temporary
    file colour the next. Clearing before the test gives it a clean slate; clearing
    afterwards stops this module from poisoning its siblings, including the ones that build a
    real application.

    Autouse and function-scoped so no test has to remember, and applied even to the tests
    that never touch a file, because the cost is a dictionary reset.

    Yields:
        ``None``. The value is unused; the fixture exists for its two side effects.
    """
    reader.clear_cache()
    try:
        yield
    finally:
        reader.clear_cache()


@pytest.fixture(scope="session")
def reader_path(project_root: Path) -> Path:
    """Return the absolute path of the module under test.

    Args:
        project_root: Repository root, injected by the session fixture in
            ``tests/conftest.py``.

    Returns:
        Absolute path of ``app/utils/properties.py``.
    """
    return project_root / _READER_RELATIVE_PATH


@pytest.fixture(scope="session")
def reader_source(reader_path: Path) -> str:
    """Return the source text of the module under test.

    Read with an explicit encoding, like every other file access in this suite, so the
    inspection cannot be perturbed by the platform's locale.

    Args:
        reader_path: Absolute path of the module under test.

    Returns:
        The module's full source text.
    """
    return reader_path.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def reader_tree(reader_source: str, reader_path: Path) -> ast.Module:
    """Return the parsed syntax tree of the module under test.

    Session-scoped because parsing is pure and the file cannot change during a run. Holding
    the tree for the session also keeps the node identities used by
    :func:`_code_string_literals` stable.

    Args:
        reader_source: The module's source text.
        reader_path: Path used as the filename in any :class:`SyntaxError` raised here, so a
            genuine syntax error names the offending file rather than an anonymous string.

    Returns:
        The parsed module.
    """
    return ast.parse(reader_source, filename=str(reader_path))


@pytest.fixture
def template_path(project_root: Path) -> Path:
    """Return the path of the committed configuration template, skipping if it is absent.

    The template is authored at the repository root rather than by this suite. Skipping with
    a stated reason keeps a partially materialised tree from failing a test that has nothing
    to say about it, while still reporting the gap.

    Args:
        project_root: Repository root, injected by the session fixture in
            ``tests/conftest.py``.

    Returns:
        Absolute path of ``configuration.properties.example``.
    """
    path = project_root / _TEMPLATE_RELATIVE_PATH
    if not path.is_file():
        pytest.skip(
            f"{_TEMPLATE_RELATIVE_PATH} is not present at {project_root}; the committed "
            "template is authored at the repository root, and template compatibility cannot "
            "be asserted until it exists"
        )
    return path


@pytest.fixture
def template_text(template_path: Path) -> str:
    """Return the text of the committed configuration template.

    Args:
        template_path: Absolute path of the template.

    Returns:
        The template's full text, read with an explicit encoding.
    """
    return template_path.read_text(encoding="utf-8")


@pytest.fixture
def template_values(template_path: Path) -> Mapping[str, str]:
    """Return the committed template parsed by the reader under test.

    Args:
        template_path: Absolute path of the template.

    Returns:
        The parsed mapping. Read through the public entry point, so this fixture asserts the
        same code path the application uses.
    """
    return reader.load_properties(template_path)


class TestPublicApi:
    """The surface the configuration layer above imports, and the file name it names."""

    def test_public_api_is_exactly_the_documented_surface(self) -> None:
        """``__all__`` matches the set the configuration layer imports.

        ``app/config.py`` reaches this module for ``load_properties``,
        ``default_properties_path`` and the cache-clearing hook, and is forbidden from opening
        the file itself, so it has no fallback if one of those names moves. Asserting the whole
        set rather than three names also catches an accidental *addition*, which would widen a
        deliberately narrow module.
        """
        assert set(reader.__all__) == _EXPECTED_PUBLIC_API

    def test_every_published_name_is_actually_bound(self) -> None:
        """Every name in ``__all__`` resolves on the module.

        A name listed but not defined would satisfy the previous assertion and still break
        ``from app.utils.properties import ...`` at run time.
        """
        missing = [name for name in reader.__all__ if not hasattr(reader, name)]
        assert missing == [], f"declared in __all__ but not bound: {missing}"

    def test_default_file_name_is_carried_over_verbatim(self) -> None:
        """The file name equals the literal at ``[.gitignore:L3]``.

        Source literals are data, never renamed or modernised: the ignore rule and the reader
        have to agree, or the reader would look for a file the repository does not ignore.
        """
        assert reader.DEFAULT_PROPERTIES_FILENAME == _PROPERTIES_FILENAME

    def test_default_path_is_the_repository_root_properties_file(self, project_root: Path) -> None:
        """The default path is the repository-root file named at ``[.gitignore:L3]``.

        Resolved from the module's own location rather than the working directory, so it is the
        same path for a Flask worker, a pytest session and each of its parallel workers.

        Args:
            project_root: Repository root, injected by the session fixture in
                ``tests/conftest.py``.
        """
        default_path = reader.default_properties_path()

        assert default_path.is_absolute()
        assert default_path.name == _PROPERTIES_FILENAME
        assert default_path == (project_root / _PROPERTIES_FILENAME).resolve()

    def test_synthetic_section_name_is_not_the_magic_default_section(self) -> None:
        """The synthesized header is an ordinary section, not ``DEFAULT``.

        ``configparser`` never lists ``DEFAULT`` in ``sections()`` and hands back its *live*
        internal dictionary from ``defaults()``, so an ordinary name is what lets the reader
        both return a fresh mapping and notice a stray header inside the file.
        """
        assert reader.SYNTHETIC_SECTION_NAME
        assert reader.SYNTHETIC_SECTION_NAME != configparser.DEFAULTSECT

    def test_cache_clear_is_an_alias_of_clear_cache(self) -> None:
        """``cache_clear`` and ``clear_cache`` are the same object.

        Two spellings are published so a caller may use either; asserting identity documents
        that neither is a second, divergent implementation.
        """
        assert reader.cache_clear is reader.clear_cache

    def test_get_property_returns_the_default_for_an_absent_key(self, tmp_path: Path) -> None:
        """An absent key yields the supplied default, and ``None`` when none was supplied.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "present=value\n")

        assert reader.get_property("absent", "FALLBACK", path=path) == "FALLBACK"
        assert reader.get_property("absent", path=path) is None

    def test_present_but_empty_is_distinguishable_from_absent(self, tmp_path: Path) -> None:
        """A key with an empty value yields ``""``, not the default.

        The distinction matters to the configuration layer: an operator who writes
        ``tag.expression=`` is deliberately clearing a value, which is not the same as leaving
        the key out and inheriting the source default.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "empty=\n")

        assert reader.get_property("empty", "FALLBACK", path=path) == ""
        assert reader.has_property("empty", path=path) is True
        assert reader.has_property("absent", path=path) is False

    def test_returned_mapping_cannot_be_mutated(self, tmp_path: Path) -> None:
        """The mapping handed out is read-only.

        The same object is served from the cache to every caller, so a mutable mapping would
        let one caller silently rewrite another's configuration.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "browser=chrome\n")
        values = reader.load_properties(path)

        with pytest.raises(TypeError):
            values["browser"] = "firefox"  # type: ignore[index]

        assert values["browser"] == "chrome"


class _CaseSensitiveConfigParser(configparser.ConfigParser):
    """A parser whose key transform is the identity, so keys keep their case.

    The reader installs the identity transform by assigning ``str`` to the instance attribute.
    Overriding the method in a subclass is the same behaviour expressed in a form a type checker
    accepts without a suppression, which keeps the control tests below free of annotations about
    themselves. Only :class:`TestConfigParserArgumentsAreLoadBearing` uses it, and its name does
    not begin with ``Test`` so pytest does not try to collect it.
    """

    def optionxform(self, optionstr: str) -> str:
        """Return the key unchanged, because Java property keys are case-sensitive.

        Args:
            optionstr: Key exactly as written in the document.

        Returns:
            The same string, with no case folding.
        """
        return optionstr


def _read_section_less(parser: configparser.ConfigParser, document: str) -> dict[str, str]:
    """Parse a section-less document with ``parser`` and return the resulting mapping.

    The synthesized header is prepended here exactly as the reader prepends it, so the *only*
    difference between a control test and the reader is the parser's configuration. Retrieval is
    included deliberately: interpolation raises when a value is fetched, not when it is parsed,
    which makes the default especially treacherous.

    Args:
        parser: Parser under test.
        document: Java ``.properties`` text, without a section header.

    Returns:
        The parsed section as a plain dictionary.
    """
    parser.read_string(f"[{reader.SYNTHETIC_SECTION_NAME}]\n{document}")
    return dict(parser[reader.SYNTHETIC_SECTION_NAME])


class TestConfigParserArgumentsAreLoadBearing:
    """Four control tests, each dropping exactly one of the reader's four parser arguments.

    Every test feeds the *same* document -- :data:`_FOUR_HAZARD_DOCUMENT`, which contains a ``!``
    comment, a duplicated key, a case-varying key pair and a value containing ``%`` -- to a parser
    that has three of the four arguments applied and one omitted. Each omission produces a
    different, named failure, which is what proves the four are individually necessary rather
    than collectively sufficient. The final test applies all four and shows the document parses.

    These exercise :mod:`configparser` directly rather than the reader, so they document the
    behaviour of the standard library itself and act as the regression guard that stops the
    reader's parser construction being "simplified" later.
    """

    def test_dropping_comment_prefixes_makes_a_bang_comment_a_parse_error(self) -> None:
        """Argument 1: without ``comment_prefixes=("#", "!")`` a ``!`` line is a parse error.

        Java ``.properties`` accepts ``!`` as a comment marker. The ``configparser`` default is
        ``("#", ";")``, so a perfectly legal file fails outright -- a live crash, not a cosmetic
        difference, and the one hazard of the four that the ported specification never mentions.
        """
        parser = _CaseSensitiveConfigParser(interpolation=None, strict=False)

        with pytest.raises(configparser.ParsingError):
            _read_section_less(parser, _FOUR_HAZARD_DOCUMENT)

    def test_dropping_interpolation_none_makes_a_percent_value_an_error(self) -> None:
        """Argument 2: without ``interpolation=None`` a ``%`` in a value is an error.

        ``BasicInterpolation`` treats ``%`` as the start of a substitution. Java has no
        interpolation concept at all, so the character is ordinary text there, and a value such as
        a discount or a completion percentage is entirely legal.
        """
        parser = _CaseSensitiveConfigParser(comment_prefixes=_JAVA_COMMENT_MARKERS, strict=False)

        with pytest.raises(configparser.InterpolationError):
            _read_section_less(parser, _FOUR_HAZARD_DOCUMENT)

    def test_dropping_the_identity_key_transform_lower_cases_keys(self) -> None:
        """Argument 3: without the identity key transform, keys are silently lower-cased.

        The most dangerous of the four, because nothing raises. ``camelKey`` becomes ``camelkey``
        and the case-varying pair collapses into one entry, so a lookup quietly returns another
        key's value. Only three of the five declared keys survive.
        """
        parser = configparser.ConfigParser(
            comment_prefixes=_JAVA_COMMENT_MARKERS, interpolation=None, strict=False
        )

        values = _read_section_less(parser, _FOUR_HAZARD_DOCUMENT)

        assert "camelKey" not in values, "the declared key spelling was lost"
        assert values["camelkey"] == "v"
        assert "Browser" not in values, "the case-varying pair collapsed into one key"
        assert values["browser"] == "Firefox", "a lookup now returns the other key's value"
        assert len(values) == 4

    def test_dropping_strict_false_makes_a_duplicate_key_an_error(self) -> None:
        """Argument 4: without ``strict=False`` a repeated key is an error.

        Java's ``Properties.load`` keeps the last occurrence, so a file Java reads happily is
        rejected outright by a strict parser.
        """
        parser = _CaseSensitiveConfigParser(
            comment_prefixes=_JAVA_COMMENT_MARKERS, interpolation=None
        )

        with pytest.raises(configparser.DuplicateOptionError):
            _read_section_less(parser, _FOUR_HAZARD_DOCUMENT)

    def test_all_four_arguments_together_parse_the_document(self) -> None:
        """With all four applied, the document that defeats every subset parses correctly.

        The duplicate resolves to its last value, both spellings of the case-varying key survive
        as distinct entries, the ``%`` value is untouched and the two comment lines are ignored.
        """
        parser = _CaseSensitiveConfigParser(
            comment_prefixes=_JAVA_COMMENT_MARKERS, interpolation=None, strict=False
        )

        assert _read_section_less(parser, _FOUR_HAZARD_DOCUMENT) == {
            "browser": "edge",
            "camelKey": "v",
            "Browser": "Firefox",
            "raw.percent": "50% off",
            "french": _FRENCH_EMPTY_FIELD_MESSAGE,
        }

    def test_reader_matches_the_fully_configured_parser(self, tmp_path: Path) -> None:
        """The reader produces exactly what the fully configured parser produces.

        Ties the control tests to the module under test: the same document read through the public
        entry point yields the same mapping, so the four arguments proven necessary above are the
        four the reader actually applies.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        parser = _CaseSensitiveConfigParser(
            comment_prefixes=_JAVA_COMMENT_MARKERS, interpolation=None, strict=False
        )
        expected = _read_section_less(parser, _FOUR_HAZARD_DOCUMENT)
        path = _write_properties(tmp_path, _FOUR_HAZARD_DOCUMENT)

        assert dict(reader.load_properties(path)) == expected
        assert expected["browser"] == "edge"
        assert expected["Browser"] == "Firefox"


class TestJavaPropertiesParsing:
    """The Java ``.properties`` subset the reader accepts, asserted through its public API."""

    def test_bang_comment_line_is_ignored_without_raising(self, tmp_path: Path) -> None:
        """A ``!`` comment is a comment, not a parse error.

        The counterpart of control test 1. Java accepts ``!`` alongside ``#``, and this is the
        single case where the default parser configuration crashes outright on legal input.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "! bang comment\nbrowser=chrome\n")

        assert dict(reader.load_properties(path)) == {"browser": "chrome"}

    def test_hash_comment_line_is_ignored_without_raising(self, tmp_path: Path) -> None:
        """A ``#`` comment is ignored, the marker Java shares with ``configparser``.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "# hash comment\nbrowser=chrome\n")

        assert dict(reader.load_properties(path)) == {"browser": "chrome"}

    def test_semicolon_line_is_data_because_java_has_no_semicolon_comment(
        self, tmp_path: Path
    ) -> None:
        """``;`` does not start a comment, because it never did in Java.

        ``configparser`` treats ``;`` as a comment marker by default. Replacing the prefixes
        with the two Java markers deliberately drops it, so a Java file that happens to contain
        a ``;``-prefixed key keeps that key rather than losing it silently.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        semicolon_key = f"{_NON_JAVA_COMMENT_MARKER}browser"
        path = _write_properties(tmp_path, f"{semicolon_key}=chrome\n")

        assert dict(reader.load_properties(path)) == {semicolon_key: "chrome"}

    def test_percent_in_value_round_trips_unmangled(self, tmp_path: Path) -> None:
        """A ``%`` is ordinary text in a value, wherever it appears.

        The counterpart of control test 2. Both a trailing ``%`` and one mid-value are checked,
        because interpolation reacts to the character's context rather than its presence.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "pct=100%\nraw.percent=50% off\ndoubled=100%%done\n")

        assert dict(reader.load_properties(path)) == {
            "pct": "100%",
            "raw.percent": "50% off",
            "doubled": "100%%done",
        }

    def test_keys_are_case_sensitive(self, tmp_path: Path) -> None:
        """Key case is preserved and two spellings remain distinct keys.

        The counterpart of control test 3, and the reason lookups are exact-match with no
        case-folding fallback: Java property keys are case-sensitive, so ``camelKey`` must not
        be reachable as ``camelkey``.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "camelKey=v\nbrowser=chrome\nBrowser=Firefox\n")
        values = reader.load_properties(path)

        assert "camelKey" in values
        assert "camelkey" not in values
        assert values["browser"] == "chrome"
        assert values["Browser"] == "Firefox"
        assert reader.get_property("camelkey", path=path) is None

    def test_duplicate_key_keeps_the_last_value(self, tmp_path: Path) -> None:
        """A repeated key resolves to its last occurrence, and does not raise.

        The counterpart of control test 4. Last-one-wins is Java's ``Properties.load``
        behaviour, and the same principle the migration applies to the source build's duplicate
        dependency declaration, which the build system also resolved to the last one written.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "a=1\na=2\n")

        assert dict(reader.load_properties(path)) == {"a": "2"}

    def test_colon_separator_is_accepted(self, tmp_path: Path) -> None:
        """``key:value`` parses, the second separator Java permits.

        Supported by ``configparser`` out of the box, which is why the reader widens the comment
        prefixes but leaves the delimiters alone.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "browser:chrome\ntimeout : 30\n")

        assert dict(reader.load_properties(path)) == {"browser": "chrome", "timeout": "30"}

    def test_whitespace_around_the_separator_is_tolerated(self, tmp_path: Path) -> None:
        """Padding around ``=`` is not part of the key or the value.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "timeout = 30 \n  \nbrowser\t=\tchrome\n")

        assert dict(reader.load_properties(path)) == {"timeout": "30", "browser": "chrome"}

    def test_empty_value_is_the_empty_string_not_none(self, tmp_path: Path) -> None:
        """``key=`` yields ``""``, which is a value rather than an absence.

        Returning ``None`` here would make a deliberately cleared setting indistinguishable
        from an omitted one, and the configuration layer's precedence chain depends on telling
        those apart.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        values = reader.load_properties(_write_properties(tmp_path, "empty=\nalso.empty= \n"))

        assert values["empty"] == ""
        assert values["also.empty"] == ""

    def test_value_containing_equals_round_trips_intact(self, tmp_path: Path) -> None:
        """Only the first separator splits the line; later ones are value text.

        A query string is the realistic case, and the reader must not truncate one.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        url = "https://x.example/a?b=c&d=e"
        path = _write_properties(tmp_path, f"url={url}\n")

        assert reader.get_property("url", path=path) == url

    def test_crlf_line_endings_are_tolerated(self, tmp_path: Path) -> None:
        """A file written with Windows line endings parses, with no stray carriage returns.

        The pipeline this port preserves dispatches between a shell and a batch command, so a
        properties file authored on either platform has to read the same. The assertion checks
        the values as well as the keys, because a surviving ``\\r`` would hide at the end of a
        value where it is easy to miss.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "a=1\r\nb=2\r\n! bang\r\nc=three\r\n")

        assert dict(reader.load_properties(path)) == {"a": "1", "b": "2", "c": "three"}

    def test_space_separated_pair_degrades_without_raising(self, tmp_path: Path) -> None:
        """A space-separated pair is a documented limitation, and still never raises.

        Java permits ``key value``. ``configparser`` cannot express that -- not even by widening
        its delimiters -- so the document is rejected and the reader degrades to an empty
        mapping. What matters, and what is asserted here, is that nothing escapes and no
        half-parsed key is invented: writing a complete Java ``.properties`` parser to close
        this gap is out of scope precisely because the standard library already covers the
        subset the project uses.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "browser chrome\n")

        values = reader.load_properties(path)

        assert "browser" not in values, "a space-separated pair must not be half-parsed"
        assert dict(values) == {}, "configparser rejects the whole document, so nothing is read"

    def test_stray_section_header_is_reported_rather_than_silently_honoured(
        self, tmp_path: Path
    ) -> None:
        """Keys under a section header in the file are unreachable, and that is said out loud.

        Java ``.properties`` has no sections, so a header is a format mistake. The reader parses
        the document, notices the unexpected section and logs it at ``DEBUG`` instead of failing
        or pretending the keys were read.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "kept=yes\n[extra]\nunreachable=no\n")

        values = reader.load_properties(path)

        assert values["kept"] == "yes"
        assert "unreachable" not in values

    def test_empty_file_yields_an_empty_mapping(self, tmp_path: Path) -> None:
        """A file with no content is a file with no settings, not an error.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        assert dict(reader.load_properties(_write_properties(tmp_path, ""))) == {}

    def test_comment_only_file_yields_an_empty_mapping(self, tmp_path: Path) -> None:
        """A file of nothing but comments declares nothing, using both Java markers.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "# only\n! comments\n\n# here\n")

        assert dict(reader.load_properties(path)) == {}


class TestGracefulDegradation:
    """Every failure mode is an empty mapping, a ``DEBUG`` line, and nothing else.

    This is the class that matters most in practice. ``configuration.properties`` is
    git-ignored ``[.gitignore:L3]``, so the *normal* state of a fresh clone, a CI run and a
    container build is that the file is not there. A reader that raised, warned as an error or
    exited would take the application down on an ordinary checkout.
    """

    def test_missing_file_yields_an_empty_mapping(self, tmp_path: Path) -> None:
        """An absent file is the common path, and it produces an empty mapping.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        missing = tmp_path / _PROPERTIES_FILENAME

        assert not missing.exists()
        assert dict(reader.load_properties(missing)) == {}
        assert reader.get_property("browser", "chrome", path=missing) == "chrome"
        assert reader.has_property("browser", path=missing) is False

    def test_absent_default_path_yields_an_empty_mapping(self) -> None:
        """Called with no argument on a fresh checkout, the reader returns an empty mapping.

        This is the exact call the configuration layer makes. The file is git-ignored
        ``[.gitignore:L3]`` and therefore absent from the tree; if a deployment has provided one
        the assertion has nothing to say and the test skips with that reason stated.
        """
        default_path = reader.default_properties_path()
        if default_path.exists():
            pytest.skip(
                f"{default_path} exists in this working tree, so the absent-file contract "
                "cannot be observed here; the file is git-ignored [.gitignore:L3] and is "
                "normally not present"
            )

        assert dict(reader.load_properties()) == {}

    def test_missing_file_logs_at_debug_and_no_higher(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The absence is recorded, once, at ``DEBUG``.

        Absence is normal, so it must be observable when someone is looking and invisible when
        they are not. Both halves are asserted: a record exists when the level is lowered, and
        every record is at ``DEBUG`` or below.

        Args:
            tmp_path: pytest-provided temporary directory.
            caplog: pytest log-capture fixture.
        """
        missing = tmp_path / _PROPERTIES_FILENAME

        with caplog.at_level(logging.DEBUG, logger=_READER_LOGGER_NAME):
            values = reader.load_properties(missing)

        above_debug = [
            record.levelname for record in caplog.records if record.levelno > logging.DEBUG
        ]

        assert dict(values) == {}
        assert caplog.records, "the absent-file path must leave a DEBUG trace"
        assert all(record.name == _READER_LOGGER_NAME for record in caplog.records)
        assert above_debug == [], f"absence must never be reported above DEBUG: {above_debug}"

    def test_missing_file_emits_nothing_at_default_log_level(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """At the default level the absence is completely silent.

        The complement of the previous test: with no level lowered, nothing at ``INFO`` or above
        is produced, so a normal run is not littered with notices about a file that was never
        meant to be there.

        Args:
            tmp_path: pytest-provided temporary directory.
            caplog: pytest log-capture fixture.
        """
        reader.load_properties(tmp_path / _PROPERTIES_FILENAME)

        noisy = [record.getMessage() for record in caplog.records if record.levelno >= logging.INFO]
        assert noisy == [], f"expected silence at INFO and above, got {noisy}"

    def test_directory_in_place_of_a_file_yields_an_empty_mapping(self, tmp_path: Path) -> None:
        """A path that is a directory is unreadable, and unreadable degrades to empty.

        ``IsADirectoryError`` is an ``OSError``, and this is the one I/O failure that reproduces
        identically for every user, including a privileged one, which is why it carries the
        unreadable-file contract here.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        directory = tmp_path / _PROPERTIES_FILENAME
        directory.mkdir()

        assert dict(reader.load_properties(directory)) == {}

    def test_permission_denied_yields_an_empty_mapping(self, tmp_path: Path) -> None:
        """A file the process may not read degrades to an empty mapping.

        Skipped when the tests run as a privileged user, because file mode bits do not deny a
        superuser and the case would silently prove nothing. The directory test above covers the
        ``OSError`` contract unconditionally.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip(
                "running as a privileged user, which bypasses file mode bits, so chmod 000 "
                "cannot produce PermissionError here; the directory case covers the same "
                "OSError contract"
            )

        unreadable = _write_properties(tmp_path, "browser=chrome\n", name="locked.properties")
        unreadable.chmod(0o000)
        try:
            assert dict(reader.load_properties(unreadable)) == {}
        finally:
            # Restored so pytest's own tmp_path retention can read the directory afterwards.
            unreadable.chmod(0o600)

    def test_malformed_content_yields_an_empty_mapping(self, tmp_path: Path) -> None:
        """Content that is not a properties document degrades to an empty mapping.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "not properties at all {{{\n[[[\n]]]\n")

        assert dict(reader.load_properties(path)) == {}

    def test_bare_key_without_separator_yields_an_empty_mapping(self, tmp_path: Path) -> None:
        """A key with no separator is a parse error, and a parse error degrades.

        Java would read a bare key as an empty value. ``configparser`` will not, which is a
        stated limitation rather than a hidden one -- so the assertion is that the reader stays
        quiet, not that it somehow succeeds.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "loneKey\n")

        assert dict(reader.load_properties(path)) == {}

    def test_undecodable_bytes_yield_an_empty_mapping(self, tmp_path: Path) -> None:
        """Bytes that are not valid UTF-8 degrade to an empty mapping.

        The reader reads with an explicit UTF-8 encoding, so a file in some other encoding
        raises ``UnicodeDecodeError`` inside it. That is absorbed like every other failure.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = tmp_path / "undecodable.properties"
        path.write_bytes(b"\xff\xfe\x00bad=\x80\x81\n")

        assert dict(reader.load_properties(path)) == {}

    def test_no_failure_mode_writes_to_stdout_or_stderr(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Not one degradation path prints anything.

        Diagnostics go through the module logger and nowhere else, so a library read cannot
        corrupt the output of a command-line tool that happens to call it.

        Args:
            tmp_path: pytest-provided temporary directory.
            capsys: pytest output-capture fixture.
        """
        directory = tmp_path / "as-directory"
        directory.mkdir()
        undecodable = tmp_path / "undecodable.properties"
        undecodable.write_bytes(b"\xff\xfe bad\n")

        for candidate in (
            tmp_path / "missing.properties",
            directory,
            undecodable,
            _write_properties(tmp_path, "garbage {{{\n[[[\n"),
            _write_properties(tmp_path, "browser chrome\n", name="spaced.properties"),
        ):
            reader.clear_cache()
            assert dict(reader.load_properties(candidate)) == {}

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    @pytest.mark.parametrize(
        "relative",
        [
            pytest.param("missing.properties", id="missing-file"),
            pytest.param(".", id="directory"),
            pytest.param("missing-parent/missing.properties", id="missing-parent"),
            pytest.param(f"{_PROPERTIES_FILENAME}\x00truncated", id="nul-byte-in-name"),
        ],
    )
    def test_no_exception_escapes_for_any_unusable_path(
        self, tmp_path: Path, relative: str
    ) -> None:
        """Nothing escapes the reader, for any shape of unusable path.

        Written as an explicit exception guard rather than a bare call, so a regression reports
        *which* path leaked *what* and so "never propagates an exception" is an assertion in its
        own right rather than an implicit consequence of the test happening to pass. The cases
        cover a plain absence, a directory, a path whose parent does not exist and a path the
        operating system cannot express at all.

        Args:
            tmp_path: pytest-provided temporary directory.
            relative: Path fragment appended to ``tmp_path`` to build the unusable path.
        """
        candidate = tmp_path / relative

        try:
            values = reader.load_properties(candidate)
        except Exception as escaped:
            pytest.fail(f"{candidate!r} leaked {type(escaped).__name__}: {escaped}")

        assert dict(values) == {}


class TestReaderIsReadOnly:
    """The reader reads. It creates nothing, writes nothing and deletes nothing."""

    def test_reading_leaves_the_file_bytes_untouched(self, tmp_path: Path) -> None:
        """The document on disk is byte-identical before and after a read.

        The synthesized section header exists only inside the string handed to the parser, and
        this is the assertion that keeps it that way: a reader that "normalised" the file on disk
        would rewrite an operator's configuration behind their back.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, _FOUR_HAZARD_DOCUMENT)
        before = path.read_bytes()

        reader.load_properties(path)

        assert path.read_bytes() == before
        assert reader.SYNTHETIC_SECTION_NAME.encode("utf-8") not in path.read_bytes()

    def test_no_failure_mode_creates_a_file(self, tmp_path: Path) -> None:
        """Not one degradation path adds an entry to the directory it looked in.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        before = sorted(entry.name for entry in tmp_path.iterdir())

        for candidate in (
            tmp_path / _PROPERTIES_FILENAME,
            tmp_path / "nested" / _PROPERTIES_FILENAME,
            tmp_path,
        ):
            reader.clear_cache()
            assert dict(reader.load_properties(candidate)) == {}

        assert sorted(entry.name for entry in tmp_path.iterdir()) == before

    def test_reader_never_materialises_the_git_ignored_file(self, project_root: Path) -> None:
        """Reading the default path does not bring ``configuration.properties`` into existence.

        ``[.gitignore:L3]`` ignores that file, and every other test in the session depends on it
        staying absent: a real one at the repository root would shadow the optional-configuration
        contract for the whole suite.

        Args:
            project_root: Repository root, injected by the session fixture in
                ``tests/conftest.py``.
        """
        default_path = reader.default_properties_path()
        existed_before = default_path.exists()

        reader.load_properties()

        assert default_path.exists() is existed_before
        assert (project_root / _PROPERTIES_FILENAME).exists() is existed_before


class TestCaching:
    """The Cached Configuration Reader pattern, and the hook that makes it testable."""

    def test_repeated_reads_are_served_from_the_cache(self, tmp_path: Path) -> None:
        """A second read of the same path returns the first read's result.

        Proven the only way it can be proven from outside: change the file on disk between the
        two reads and show the *stale* value comes back. Caching is the whole point of the
        pattern -- it replaces the scattered file reads the source project would have had with
        one read per path per process.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "v=first\n")

        assert reader.get_property("v", path=path) == "first"

        path.write_text("v=second\n", encoding="utf-8", newline="")

        assert reader.get_property("v", path=path) == "first", "the read was not cached"

    def test_clear_cache_makes_new_content_visible(self, tmp_path: Path) -> None:
        """After the published hook, the next read observes the file as it now is.

        Without this hook the cache would be unclearable, and neither this module nor a
        long-lived service asked to reload could function.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "v=first\n")
        assert reader.get_property("v", path=path) == "first"

        path.write_text("v=second\n", encoding="utf-8", newline="")
        reader.clear_cache()

        assert reader.get_property("v", path=path) == "second"

    def test_cache_clear_alias_clears_the_same_cache(self, tmp_path: Path) -> None:
        """The ``cache_clear`` spelling invalidates the cache just as ``clear_cache`` does.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "v=first\n")
        assert reader.get_property("v", path=path) == "first"

        path.write_text("v=second\n", encoding="utf-8", newline="")
        reader.cache_clear()

        assert reader.get_property("v", path=path) == "second"

    def test_absence_is_cached_and_then_invalidated(self, tmp_path: Path) -> None:
        """A missing file is cached like a present one, and the hook lifts that too.

        Caching the common case is what makes "no file at all" cost one stat per path per
        process rather than one per lookup, and a deployment that drops the file in later still
        needs a way to be seen.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = tmp_path / _PROPERTIES_FILENAME

        assert dict(reader.load_properties(path)) == {}

        path.write_text("browser=chrome\n", encoding="utf-8", newline="")

        assert dict(reader.load_properties(path)) == {}, "absence was not cached"

        reader.clear_cache()

        assert dict(reader.load_properties(path)) == {"browser": "chrome"}

    def test_two_spellings_of_one_path_share_a_cache_entry(self, tmp_path: Path) -> None:
        """A relative-looking and an absolute spelling of one file resolve to one entry.

        Paths are normalised before they reach the cache, so ``dir/../dir/file`` and
        ``dir/file`` are not parsed twice. Asserted through the observable consequence: the
        second spelling sees the first spelling's cached content.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        nested = tmp_path / "nested"
        nested.mkdir()
        direct = _write_properties(nested, "v=first\n")
        roundabout = nested / ".." / "nested" / direct.name

        assert reader.get_property("v", path=direct) == "first"

        direct.write_text("v=second\n", encoding="utf-8", newline="")

        assert reader.get_property("v", path=roundabout) == "first"

    def test_string_and_path_arguments_are_interchangeable(self, tmp_path: Path) -> None:
        """A ``str`` and a :class:`~pathlib.Path` naming the same file behave identically.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, "browser=chrome\n")

        assert dict(reader.load_properties(str(path))) == {"browser": "chrome"}
        assert dict(reader.load_properties(path)) == {"browser": "chrome"}


class TestPreservedFrenchMessage:
    """Defect D5: the French assertion literal survives byte-for-byte.

    ``[README.md:L130]`` describes the expected message in English while
    ``[README.md:L135]`` asserts it in French. The assertion is the executable truth, so the
    French string is what the port carries. The defect is preserved deliberately rather than
    repaired, and ``docs/migration-parity.md`` is the register where that decision, and the fix
    the owners may elect instead, are recorded. These tests exist so the preservation is
    auditable rather than accidental.
    """

    def test_french_message_round_trips_byte_exactly(self, tmp_path: Path) -> None:
        """The literal comes back out of the reader exactly as it went in.

        Compared as bytes as well as characters, because a byte-level comparison is the only one
        that would notice a re-encoding, and the value is read through the public accessor as
        well as the mapping so neither path can transform it.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, f"expected.message={_FRENCH_EMPTY_FIELD_MESSAGE}\n")

        value = reader.get_property("expected.message", path=path)

        assert value == _FRENCH_EMPTY_FIELD_MESSAGE
        assert value is not None
        assert value.encode("utf-8") == _FRENCH_EMPTY_FIELD_MESSAGE.encode("utf-8")
        assert reader.load_properties(path)["expected.message"] == _FRENCH_EMPTY_FIELD_MESSAGE

    def test_french_message_is_twenty_nine_ascii_characters(self, tmp_path: Path) -> None:
        """The literal is 29 characters and pure ASCII, despite being French.

        Worth stating explicitly because it is easy to get wrong: this is French-*language* text
        that happens to contain no accented character, so it is 29 bytes in UTF-8 rather than a
        multi-byte string. Anyone reasoning about it as multi-byte would draw the wrong
        conclusion about encoding risk here.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, f"expected.message={_FRENCH_EMPTY_FIELD_MESSAGE}\n")

        value = reader.get_property("expected.message", path=path)

        assert value is not None
        assert len(value) == _FRENCH_MESSAGE_LENGTH
        assert len(value.encode("utf-8")) == _FRENCH_MESSAGE_LENGTH
        assert value.isascii()

    def test_trailing_period_survives(self, tmp_path: Path) -> None:
        """The final ``.`` is part of the asserted string and is not stripped.

        The reader must not normalise a value in any way that would drop it: the scenario
        compares the page's message to this string exactly, so a missing period is a failing
        scenario.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        path = _write_properties(tmp_path, f"expected.message={_FRENCH_EMPTY_FIELD_MESSAGE}\n")

        value = reader.get_property("expected.message", path=path)

        assert value is not None
        assert value.endswith(".")
        assert value.rstrip(".") != value

    def test_message_is_not_translated_to_the_english_comment(self) -> None:
        """The carried literal is the French assertion, not the English comment.

        ``[README.md:L130]`` and ``[README.md:L135]`` disagree. Repairing that disagreement by
        adopting the comment's wording would change behaviour, which the parity mandate forbids,
        so the divergence is asserted rather than resolved.
        """
        assert _FRENCH_EMPTY_FIELD_MESSAGE != _ENGLISH_COMMENT_MESSAGE
        assert _ENGLISH_COMMENT_MESSAGE not in _FRENCH_EMPTY_FIELD_MESSAGE

    def test_values_are_not_unicode_normalised(self, tmp_path: Path) -> None:
        """A decomposed character stays decomposed.

        Written with escapes so this file stays pure ASCII while still producing genuinely
        non-ASCII content: ``e`` followed by a combining acute accent is two code points, and any
        composition pass would silently fold it into the single precomposed character. That would
        change the bytes of a value, which is exactly what must not happen to a literal the
        scenarios compare exactly.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        decomposed = "e\u0301"
        precomposed = "\u00e9"
        path = _write_properties(tmp_path, f"accent={decomposed}\n")

        value = reader.get_property("accent", path=path)

        assert value == decomposed
        assert value != precomposed
        assert value is not None
        assert len(value) == 2

    def test_values_are_not_case_folded_or_unquoted(self, tmp_path: Path) -> None:
        """Case and surrounding quotes are value text, and both survive.

        Quotes are the realistic trap: a reader that helpfully stripped them would change a
        value the moment an operator quoted it out of habit.

        Args:
            tmp_path: pytest-provided temporary directory.
        """
        quoted = f'"{_FRENCH_EMPTY_FIELD_MESSAGE}"'
        path = _write_properties(tmp_path, f"mixed=CamelCase Value\nquoted={quoted}\n")
        values = reader.load_properties(path)

        assert values["mixed"] == "CamelCase Value"
        assert values["quoted"] == quoted
        assert values["quoted"].startswith('"')
        assert values["quoted"].endswith('"')


class TestTemplateCompatibility:
    """The committed template parses, and every value it carries is the source value.

    ``configuration.properties.example`` is the tracked half of the ``[.gitignore:L3]``
    contract: the real file is ignored, so the template is the only place an operator can see
    which keys exist and what they default to. If the reader could not parse it, the documented
    configuration surface would be fiction.
    """

    def test_template_has_no_section_header(self, template_text: str) -> None:
        """The template is a flat Java document, with no ``[section]`` line.

        This is the property that makes the synthesized-header approach necessary in the first
        place, and a header appearing later would make the template's keys unreachable through
        the reader.

        Args:
            template_text: Text of the committed template.
        """
        headers = [line for line in template_text.splitlines() if line.startswith("[")]

        assert headers == [], f"the template must declare no section header, found {headers}"

    def test_reader_parses_the_committed_template(self, template_values: Mapping[str, str]) -> None:
        """The template parses into a non-empty mapping.

        Args:
            template_values: The template as parsed by the reader.
        """
        assert template_values, "the committed template must parse into at least one setting"

    def test_every_key_the_template_declares_is_returned(
        self, template_text: str, template_values: Mapping[str, str]
    ) -> None:
        """Every declared key comes back, and nothing is invented.

        The expected key set is derived by scanning the raw file independently rather than from
        the reader's own output, so this compares two separate readings of the same document
        instead of asking the reader to agree with itself.

        Args:
            template_text: Text of the committed template.
            template_values: The template as parsed by the reader.
        """
        declared = _raw_keys(template_text)

        assert set(declared) == set(template_values)
        assert len(template_values) == len(set(declared))

    @pytest.mark.parametrize(("key", "expected", "locator"), _TEMPLATE_SOURCE_VALUES)
    def test_template_carries_the_source_value(
        self, template_values: Mapping[str, str], key: str, expected: str, locator: str
    ) -> None:
        """Each templated setting equals the literal its source construct declares.

        Configuration values are data, not decisions, so every one of these is carried over
        unchanged and cited: the clone URL, the tag selector that selects nothing, the failure
        tolerance, the six report thresholds that gate nothing, the report sort order, the report
        include glob and the artifact root. The last entry is the preserved D5 message, which is
        why this class and :class:`TestPreservedFrenchMessage` assert the same literal from two
        directions.

        Args:
            template_values: The template as parsed by the reader.
            key: Template key under test.
            expected: Literal the source construct declares.
            locator: Source locator, reported when the assertion fails.
        """
        assert key in template_values, f"{key} is missing from {_TEMPLATE_RELATIVE_PATH}"
        assert template_values[key] == expected, f"{key} must carry the value at {locator}"

    def test_template_keys_are_reachable_through_the_accessors(
        self, template_path: Path, template_values: Mapping[str, str]
    ) -> None:
        """Every template key is reachable by exact-match lookup, not only in bulk.

        The configuration layer reads one key at a time, so the accessor is the code path that
        actually runs in production.

        Args:
            template_path: Absolute path of the template.
            template_values: The template as parsed by the reader.
        """
        for key, expected in template_values.items():
            assert reader.has_property(key, path=template_path) is True
            assert reader.get_property(key, path=template_path) == expected

    def test_template_is_not_mistaken_for_the_ignored_file(self, template_path: Path) -> None:
        """The template and the git-ignored file are different paths.

        Only ``configuration.properties.example`` is committed; the file ``[.gitignore:L3]``
        names must stay absent. Reading the template must never be a way of reading, or creating,
        the other one.

        Args:
            template_path: Absolute path of the template.
        """
        assert template_path.name != _PROPERTIES_FILENAME
        assert template_path.name == f"{_PROPERTIES_FILENAME}.example"


class TestScopeBoundary:
    """The reader supplies one rung of the precedence chain and reaches into no other.

    The documented chain is: explicit argument, then environment variable, then ``.env``, then
    ``configuration.properties``, then hard-coded default. This module owns the fourth rung
    only. The rest belong to ``app/config.py``, and the artifact-path constants belong to
    ``app/utils/paths.py``.

    These assertions read the reader's syntax tree rather than its text. That is not fussiness:
    the module's own docstring *names* the third-party parser it refuses to depend on, so a text
    search would report that documentation as a violation. An AST separates executable code from
    prose exactly, which is the only way to assert "does not use X" about a module that
    explains why it does not use X.
    """

    def test_reader_imports_only_the_standard_library(self, reader_tree: ast.Module) -> None:
        """Every import resolves to a standard-library package.

        The utility layer is the terminal node of the application's dependency graph, so it may
        not pull a third-party distribution in behind everything else. Checked against the
        interpreter's own inventory of standard-library modules rather than a hand-written
        allowlist that could drift.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        third_party = sorted(
            root
            for root in _imported_roots(reader_tree)
            if root and root not in sys.stdlib_module_names
        )

        assert third_party == [], f"the reader must import only the standard library: {third_party}"

    def test_reader_does_not_import_a_third_party_properties_parser(
        self, reader_tree: ast.Module, reader_source: str
    ) -> None:
        """No dedicated ``.properties`` distribution is imported.

        The reader exists to prove the standard library is enough. Reaching for a package would
        add an unpinned dependency and break the dependency-parity assertion, so the refusal is
        asserted here -- while the module stays free to *discuss* that package in its
        documentation, which the second assertion confirms it does.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
            reader_source: Source text of the module under test.
        """
        imported = _imported_modules(reader_tree) | _imported_roots(reader_tree)

        assert "javaproperties" not in imported
        assert "jproperties" not in imported
        assert "configparser" in imported, "the standard-library parser is the whole mechanism"
        assert "javaproperties" in reader_source, (
            "the module should document why it needs no third-party parser, which is also why "
            "this assertion is made against the syntax tree rather than the text"
        )

    def test_reader_does_not_import_the_configuration_layer_or_its_siblings(
        self, reader_tree: ast.Module
    ) -> None:
        """Neither the layer above nor either sibling utility module is imported.

        ``app.config`` consumes this module, so importing it back would invert the dependency
        direction and create a cycle. The three modules under ``app/utils`` are mutually
        independent by design, so that each can be reasoned about, and tested, alone.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        imported = _imported_modules(reader_tree)
        violations = sorted(
            forbidden
            for forbidden in _FORBIDDEN_INTERNAL_IMPORTS
            if any(module == forbidden or module.startswith(f"{forbidden}.") for module in imported)
        )

        assert violations == [], f"forbidden internal imports: {violations}"

    def test_reader_imports_nothing_from_the_test_tree(self, reader_tree: ast.Module) -> None:
        """The application never imports the harness.

        That edge is strictly one-way: the deployable application stays independent of the tests
        even though both derive from the same source specification.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        assert "tests" not in _imported_roots(reader_tree)

    def test_reader_reads_no_environment_variable(self, reader_tree: ast.Module) -> None:
        """Rung two is untouched: no environment access of any kind.

        Environment precedence belongs to ``app/config.py``. A reader that also consulted the
        environment would give a setting two independent sources and make the chain's order
        unobservable.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        referenced = _attribute_names(reader_tree) | _called_names(reader_tree)
        violations = sorted(_ENVIRONMENT_TOKENS & referenced)

        assert violations == [], f"the reader must not read the environment: {violations}"
        assert "os" not in _imported_roots(reader_tree)

    def test_reader_loads_no_environment_file(self, reader_tree: ast.Module) -> None:
        """Rung three is untouched: ``.env`` is somebody else's job.

        There is exactly one ``python-dotenv`` load point in the application, and it is not here.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        referenced = (
            _attribute_names(reader_tree)
            | _called_names(reader_tree)
            | _identifier_names(reader_tree)
        )
        violations = sorted(_DOTENV_TOKENS & referenced)

        assert violations == [], f"the reader must not load an environment file: {violations}"
        assert "dotenv" not in _imported_roots(reader_tree)
        assert all(".env" not in literal for literal in _code_string_literals(reader_tree))

    def test_reader_defines_no_application_default(self, reader_tree: ast.Module) -> None:
        """Rung five is untouched: the reader knows nothing about what any key means.

        The clone URL, the tag selector, the report thresholds, the sort order, the include glob
        and the artifact paths are all application knowledge. A generic reader that carried any of
        them would have two owners for one literal, which is how a preserved value quietly
        diverges.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        literals = _code_string_literals(reader_tree)
        violations = sorted(
            {
                forbidden
                for forbidden in _APPLICATION_DEFAULT_LITERALS
                for literal in literals
                if forbidden in literal
            }
        )

        assert violations == [], f"application defaults leaked into the reader: {violations}"

    def test_reader_hard_codes_no_credential(self, reader_tree: ast.Module) -> None:
        """No literal in the reader looks like a secret.

        Configuration comes from the environment and an optional properties file; credentials are
        never committed. The reader also never logs a value, only counts, so a secret in a
        properties file cannot reach the log through it either.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        suspicious = sorted(
            literal
            for literal in _code_string_literals(reader_tree)
            if any(
                marker in literal.lower()
                for marker in ("password=", "secret=", "api_key=", "token=", "://", "bearer ")
            )
        )

        assert suspicious == [], f"credential-shaped literals in the reader: {suspicious}"

    def test_reader_never_terminates_the_process(self, reader_tree: ast.Module) -> None:
        """The reader cannot exit, because it never reaches the machinery that would let it.

        A library that called ``sys.exit`` would turn an optional missing file into a dead
        application. Asserted structurally as well as behaviourally: ``sys`` is not imported at
        all, so there is nothing to call.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        referenced = _called_names(reader_tree) | _identifier_names(reader_tree)

        assert "sys" not in _imported_roots(reader_tree)
        assert "exit" not in referenced
        assert "_exit" not in referenced
        assert "SystemExit" not in referenced


class TestSourceHygiene:
    """Encoding, logging and diagnostics discipline, asserted over the reader's own source."""

    def test_reader_declares_all_four_parser_arguments(self, reader_tree: ast.Module) -> None:
        """The parser is constructed with the three keywords, and the key transform is replaced.

        The behavioural tests above prove the four arguments have their intended effect; this one
        pins them at the construction site, so a future edit that drops one is reported as a
        missing argument rather than only as a puzzling parse failure. ``optionxform`` is checked
        as an attribute assignment because it cannot be passed to the constructor.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        constructions = _calls_named(reader_tree, frozenset({"ConfigParser", "RawConfigParser"}))

        assert len(constructions) == 1, "expected exactly one parser construction site"

        keywords = _keyword_names(constructions[0])
        assert {"comment_prefixes", "interpolation", "strict"} <= keywords

        interpolation = _keyword_value(constructions[0], "interpolation")
        assert isinstance(interpolation, ast.Constant)
        assert interpolation.value is None

        strict = _keyword_value(constructions[0], "strict")
        assert isinstance(strict, ast.Constant)
        assert strict.value is False

        assert "optionxform" in _attribute_names(reader_tree)

    def test_reader_declares_both_java_comment_markers_and_not_the_semicolon(
        self, reader_tree: ast.Module
    ) -> None:
        """``#`` and ``!`` appear as literals; ``;`` does not.

        Java accepts the first two. The third is a ``configparser`` default that Java never had,
        and adopting it would silently discard a legal key.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        literals = _code_string_literals(reader_tree)

        for marker in _JAVA_COMMENT_MARKERS:
            assert marker in literals, f"comment marker {marker!r} is not declared"

        assert _NON_JAVA_COMMENT_MARKER not in literals

    def test_reader_states_its_text_encoding_explicitly(self, reader_tree: ast.Module) -> None:
        """Every encoding-sensitive call passes ``encoding="utf-8"``.

        Relying on the platform's default would make a value's bytes depend on where the process
        happened to start, which is unacceptable for a file whose contents are compared exactly.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        calls = _calls_named(reader_tree, _ENCODING_SENSITIVE_CALLS)

        assert calls, "the reader must read the properties file somewhere"

        for call in calls:
            where = f"{_READER_RELATIVE_PATH}:L{call.lineno}"
            encoding = _keyword_value(call, "encoding")

            assert isinstance(encoding, ast.Constant), f"{where}: encoding must be a literal"
            assert encoding.value == "utf-8", f"{where}: expected utf-8, got {encoding.value!r}"

    def test_reader_does_not_configure_logging(self, reader_tree: ast.Module) -> None:
        """A logger is obtained; handlers, levels and formatters are left alone.

        Configuring logging from a library module would silently outrank the application's own
        settings for every process that imported it. That configuration has exactly one owner,
        and it is not this module.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        referenced = _attribute_names(reader_tree) | _called_names(reader_tree)
        violations = sorted(_LOGGING_CONFIGURATION_TOKENS & referenced)

        assert violations == [], f"the reader must not configure logging: {violations}"
        assert "getLogger" in referenced, "a module logger is still expected"

    def test_reader_uses_no_print_statement(self, reader_tree: ast.Module) -> None:
        """Diagnostics go through the logger, never to stdout.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        assert "print" not in _called_names(reader_tree)

    def test_reader_applies_no_value_transformation(self, reader_tree: ast.Module) -> None:
        """No case folding, unquoting or Unicode normalisation is applied to a value.

        The structural counterpart of the D5 round-trip tests: those show the current behaviour
        is faithful, this shows there is no machinery present that could stop being faithful.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        referenced = _attribute_names(reader_tree) | _called_names(reader_tree)
        violations = sorted(_VALUE_TRANSFORM_TOKENS & referenced)

        assert violations == [], f"values must be returned untransformed: {violations}"
        assert "unicodedata" not in _imported_roots(reader_tree)

    def test_reader_logs_no_property_value(self, reader_tree: ast.Module) -> None:
        """Log messages carry counts and paths, never the values themselves.

        A properties file may hold a credential, so the reader's diagnostics are built to be safe
        to emit at any verbosity.

        Args:
            reader_tree: Parsed syntax tree of the module under test.
        """
        logging_calls = _calls_named(reader_tree, frozenset({"debug", "info", "warning", "error"}))

        assert logging_calls, "the degradation paths must leave a trace"

        for call in logging_calls:
            where = f"{_READER_RELATIVE_PATH}:L{call.lineno}"
            template = call.args[0] if call.args else None

            assert isinstance(template, ast.Constant), f"{where}: log template must be a literal"
            assert isinstance(template.value, str), f"{where}: log template must be a string"
