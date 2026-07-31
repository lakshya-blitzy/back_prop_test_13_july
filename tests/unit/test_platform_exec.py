"""Unit suite over ``app/utils/platform_exec.py`` -- the ported ``isUnix()`` dispatch.

The source construct
====================
This suite asserts one construct and nothing else: the platform branch inside the CI
pipeline's ``stage('Run tests')``, reproduced verbatim from ``[Jenkins:L7-L11]``::

        if(isUnix()){
            sh "mvn clean test"
        } else {
            bat "mvn clean test"
        }

Two properties of those five lines shape every assertion below.

**Both branches ran the identical command.** ``sh "mvn clean test"`` ``[Jenkins:L8]`` and
``bat "mvn clean test"`` ``[Jenkins:L10]`` differ only in the executor -- the pipeline's
shell step versus its batch step. The ported shape is therefore one command run two ways,
and that is asserted directly rather than settled for by checking that two strings differ.

**``isUnix()`` was a binary predicate, not an operating-system matrix.** Every non-Windows
platform took the shell branch, so the port is ``platform.system() != "Windows"``
(AAP 0.3.1) and exactly two code paths may exist. A cross-platform matrix is explicitly out
of scope -- the helper "preserves the ``isUnix()`` branch only, not a matrix" (AAP 0.2.2) --
so no test here asserts a third branch: asserting one would *force* the implementation to
grow the very thing the plan forbids. What is asserted instead is that no third path exists,
and that a name differing from ``"Windows"`` only in case or spacing still takes the POSIX
branch, because the comparison is exact rather than normalised.

Reaching both branches from a single machine
============================================
Continuous integration for this project runs on Linux only, where ``platform.system()``
returns ``'Linux'``, so the Windows branch is unreachable by natural execution. The module
under test exposes an injectable seam for exactly that reason, and this suite uses it: the
explicit ``system=`` and ``is_unix=`` arguments are preferred over patching because they are
type-checkable and cannot leak between tests. Patching is used for one purpose only -- to
prove the host platform is consulted at call time and never cached at import time.

Behaviour and structure, asserted separately
============================================
Two complementary kinds of evidence appear below.

*Behavioural* tests call the public API and assert what comes back. They prove the dispatch
selects the right branch and that every result is an argument list of separate tokens.

*Structural* tests parse and read the module's own source. They exist because several
requirements are prohibitions -- no shell string, no process execution, no environment read,
no artifact-root knowledge, no exit-status policy, no dependency beyond the standard library
-- and a prohibition cannot be proved by calling a function that merely happens not to
violate it today. Those assertions run against a *code-only* projection of the source in
which docstrings and comments are blanked out, so the module stays free to *discuss*
execution, quoting and timeouts in its prose while being held to never *doing* any of it.
The projection preserves line numbering, so a failure still points at a real line.

Deliberate omissions
====================
No process is started, no script is run and no report is produced: the module under test
builds a command, and building is all that is asserted here. Executing it -- with an
explicit timeout, and with no shell -- is the service layer's duty, and the structural
assertions below are what make that separation enforceable rather than aspirational. Nothing
here reaches the external application the wider suite drives: it is out of scope and
unreachable from this environment, which is why all evidence gathered here is structural.
"""

from __future__ import annotations

import ast
import io
import platform
import sys
import tokenize
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    # Type-only imports: `from __future__ import annotations` defers every annotation, so
    # this block never executes at run time while the type checker still sees real types.
    from collections.abc import Mapping, Sequence

try:
    from app.utils import platform_exec
except ImportError as error:  # pragma: no cover - exercised only without the dependencies
    # Importing `app.utils.platform_exec` executes the parent package `app/__init__.py`
    # first, and that module is the Flask application factory. The module under test needs
    # nothing but the standard library, yet reaching it needs Flask installed. Degrading to
    # a module-level skip keeps collection of the whole tree green under a bare interpreter;
    # letting the ImportError escape would turn one absent distribution into a collection
    # error, which is precisely the failure mode this guard exists to prevent.
    pytest.skip(
        "app.utils.platform_exec is unreachable because importing the app package needs "
        f"its runtime dependencies ({type(error).__name__}: {error})",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------------------
# Search patterns, assembled from fragments on purpose.
#
# Several assertions below search another module's source for a forbidden construct. Writing
# such a needle as one literal would plant that literal in *this* file too, where the same
# prohibitions apply and where the repository's own hygiene greps would then report a false
# positive. Assembling each needle from fragments keeps the pattern searchable without ever
# spelling it out here, and keeps every occurrence defined exactly once.
# ---------------------------------------------------------------------------------------
def _pattern(*fragments: str) -> str:
    """Join ``fragments`` into a single search pattern.

    Args:
        fragments: Pieces of the pattern, in order. Splitting is purely lexical -- the
            fragments are concatenated with nothing between them.

    Returns:
        The assembled pattern.
    """
    return "".join(fragments)


_SHELL_TRUE_PATTERN: Final[str] = _pattern("shell", "=True")
_OS_SYSTEM_PATTERN: Final[str] = _pattern("os.", "system")
_SUBPROCESS_PATTERN: Final[str] = _pattern("sub", "process")
_PRINT_CALL_PATTERN: Final[str] = _pattern("pri", "nt(")
_OPEN_CALL_PATTERN: Final[str] = _pattern("open", "(")
_READ_TEXT_CALL_PATTERN: Final[str] = _pattern("read_text", "(")

# ---------------------------------------------------------------------------------------
# The module under test, and the two ported branch payloads.
# ---------------------------------------------------------------------------------------
_MODULE_RELATIVE_PARTS: Final[tuple[str, ...]] = ("app", "utils", "platform_exec.py")
_SUITE_RELATIVE_PARTS: Final[tuple[str, ...]] = ("tests", "unit", "test_platform_exec.py")

# Labels are derived from the path parts rather than restated, so a path and the name quoted
# in a failure message cannot drift apart.
_MODULE_LABEL: Final[str] = "/".join(_MODULE_RELATIVE_PARTS)
_SUITE_LABEL: Final[str] = "/".join(_SUITE_RELATIVE_PARTS)

# The public surface the module documents. Held as a frozen set so an accidental addition or
# removal is caught rather than tolerated.
_EXPECTED_PUBLIC_API: Final[frozenset[str]] = frozenset(
    {
        "POSIX_TEST_COMMAND",
        "WINDOWS_TEST_COMMAND",
        "build_test_command",
        "is_unix",
        "select_command",
    }
)

# The one platform name the pipeline's `isUnix()` answered `false` for, spelled exactly as
# `platform.system()` reports it. Everything else took the shell branch.
_WINDOWS_PLATFORM_NAME: Final[str] = "Windows"

# Every name below must take the POSIX branch, and each earns its place:
#   Linux, Darwin, FreeBSD, OpenBSD, SunOS, AIX  - real `platform.system()` values
#   Java                                         - what a JVM-hosted Python reports
#   ""                                           - what `platform.system()` returns when the
#                                                  host cannot be identified at all
#   CYGWIN_NT-10.0, MSYS_NT-10.0                 - POSIX layers hosted *on* Windows, which
#                                                  the pipeline's predicate also called unix
#   windows, WINDOWS, "Windows ", " Windows"     - proof the comparison is exact: a
#                                                  case-normalised or stripped variant would
#                                                  wrongly route these to the batch branch
#   Win32                                        - proof no `sys.platform`-style prefix test
#                                                  is used, since that value starts with Win
_NON_WINDOWS_PLATFORM_NAMES: Final[tuple[str, ...]] = (
    "Linux",
    "Darwin",
    "Java",
    "FreeBSD",
    "",
    "OpenBSD",
    "SunOS",
    "AIX",
    "CYGWIN_NT-10.0",
    "MSYS_NT-10.0",
    "windows",
    "WINDOWS",
    "Windows ",
    " Windows",
    "Win32",
)

# The two ported script payloads, named by their file names so each assertion cites the
# pipeline step it preserves.
_POSIX_SCRIPT_NAME: Final[str] = "run_tests.sh"  # [Jenkins:L8]  sh  "mvn clean test"
_WINDOWS_SCRIPT_NAME: Final[str] = "run_tests.bat"  # [Jenkins:L10] bat "mvn clean test"
_SCRIPT_DIRECTORY: Final[str] = "scripts"
_SHARED_COMMAND_STEM: Final[str] = "run_tests"

# Characters and operators that only mean anything to a shell. None may appear inside an
# argv token, because a token carrying one is a command *line* rather than an argument.
_SHELL_METACHARACTERS: Final[tuple[str, ...]] = (
    "&&",
    "||",
    "&",
    "|",
    ";",
    ">",
    "<",
    "$",
    "`",
    "\n",
    "\r",
    "\t",
)

# Quoting an argv token would corrupt the argument the callee finally receives, because no
# shell is involved to strip the quotes again.
_QUOTE_CHARACTERS: Final[tuple[str, ...]] = ("'", '"')

# ---------------------------------------------------------------------------------------
# Prohibitions, as tables of pattern -> why it is forbidden.
#
# Each table maps a construct that must not appear in the module's *executable* source to
# the reason it is banned, so a failure explains itself instead of merely reporting a string
# match. Every table is searched against the code-only projection built below, never against
# the raw text: the module is expected to write about execution and quoting in its prose.
# ---------------------------------------------------------------------------------------
_FORBIDDEN_EXECUTION_PATTERNS: Final[Mapping[str, str]] = {
    _SHELL_TRUE_PATTERN: (
        "a shell would restore the implicit shell semantics of the pipeline's sh and bat "
        "steps [Jenkins:L8, L10] that this port replaces with explicit argument lists"
    ),
    _OS_SYSTEM_PATTERN: "running a command string through the shell is what an argv list replaces",
    _SUBPROCESS_PATTERN: (
        "this module builds the command; the service layer executes it, with the explicit "
        "timeout that belongs to execution rather than to construction"
    ),
    "Popen": "starting a process here would erase the build/execute boundary",
    "check_output": "starting a process here would erase the build/execute boundary",
    "os.exec": "replacing the running process is execution, not command construction",
    "os.spawn": "starting a process here would erase the build/execute boundary",
    "shlex": (
        "quoting is unnecessary for an argument list and would corrupt the arguments the "
        "callee finally receives"
    ),
    '" ".join': "joining tokens produces the single command line an argv list exists to avoid",
    "' '.join": "joining tokens produces the single command line an argv list exists to avoid",
}

_FORBIDDEN_PLATFORM_PROBE_PATTERNS: Final[Mapping[str, str]] = {
    "os.name": 'the ported decision is platform.system() != "Windows", not an os.name test',
    "sys.platform": 'the ported decision is platform.system() != "Windows", not a sys.platform test',
    ".lower()": "the comparison is exact; a case-normalised variant is a different predicate",
    ".upper()": "the comparison is exact; a case-normalised variant is a different predicate",
    "casefold": "the comparison is exact; a case-normalised variant is a different predicate",
    ".strip()": "the comparison is exact; a whitespace-stripped variant is a different predicate",
    "startswith": "a prefix test would treat names such as Win32 as Windows, which it must not",
}

_FORBIDDEN_CONFIGURATION_PATTERNS: Final[Mapping[str, str]] = {
    "os.environ": "environment lookup is a rung of the configuration precedence chain, owned "
    "by app/config.py",
    "getenv": "environment lookup is a rung of the configuration precedence chain, owned by "
    "app/config.py",
    "dotenv": "reading the .env file is app/config.py's rung of the precedence chain",
    "configparser": "reading configuration.properties belongs to app/utils/properties.py",
    "ConfigParser": "reading configuration.properties belongs to app/utils/properties.py",
}

_FORBIDDEN_FILESYSTEM_PATTERNS: Final[Mapping[str, str]] = {
    "mkdir": "creating the artifact tree has exactly three owners, and none of them is here",
    "makedirs": "creating the artifact tree has exactly three owners, and none of them is here",
    _OPEN_CALL_PATTERN: "a command builder reads no file",
    "exists(": "a command builder inspects no path",
    "unlink": "nothing is deleted anywhere in this port",
    "rmtree": "nothing is deleted anywhere in this port",
    "target/": "the artifact root is app/utils/paths.py's knowledge, not this module's",
}

_FORBIDDEN_EXIT_CODE_PATTERNS: Final[Mapping[str, str]] = {
    "returncode": "exit-status policy belongs to the test runner service, which maps pytest's "
    "codes deliberately",
    "exit_code": "exit-status policy belongs to the test runner service, which maps pytest's "
    "codes deliberately",
    "sys.exit": "a library module never terminates its host process",
    "CalledProcessError": "no process is started here, so none can fail here",
    "TimeoutExpired": "the timeout belongs to the layer that executes the command",
}

_FORBIDDEN_FRAMEWORK_PATTERNS: Final[Mapping[str, str]] = {
    "ABC": "the dispatch reduces to a predicate and a command builder; an abstract base class "
    "would be an abstraction the plan does not ask for",
    "abstractmethod": "a predicate plus a command builder needs no abstract method",
    "Enum": "two branches need no enumeration type",
    "register(": "two branches need no registry or plugin hook",
    "entry_point": "two branches need no plugin discovery",
}

# Import roots the module may never pull in. Each maps to the prohibition it would make
# reachable: without these, the constructs banned above cannot even be spelled.
_FORBIDDEN_IMPORT_ROOTS: Final[Mapping[str, str]] = {
    _SUBPROCESS_PATTERN: "process execution belongs to the service layer",
    "shlex": "argument lists make quoting unnecessary",
    "os": "would make environment reads, os.name probing and shell invocation reachable",
    "sys": "would make sys.platform probing and process termination reachable",
    "shutil": "no file is copied, moved or removed by a command builder",
    "pathlib": "path knowledge belongs to app/utils/paths.py",
    "abc": "no abstract base class belongs in a two-branch dispatch",
    "enum": "no enumeration type belongs in a two-branch dispatch",
    "argparse": "this module parses no command line; it builds one",
    "configparser": "reading configuration files belongs to app/utils/properties.py",
    "app": "app/utils is the terminal node of api -> services -> reporting -> utils",
    "tests": "nothing under app/ may ever import from the test harness",
    "flask": "a command builder needs no web framework",
    "pytest": "production code never imports the test framework",
}

# Patterns that must not appear in *this* file either. The suite holds itself to the same
# discipline it asserts: no shell, no stray diagnostics on stdout, no browser automation and
# no reference to a manifest this project does not have.
_FORBIDDEN_SELF_PATTERNS: Final[Mapping[str, str]] = {
    _PRINT_CALL_PATTERN: "diagnostics belong in structured logging, never on stdout",
    _SHELL_TRUE_PATTERN: "this suite honours the argument-list discipline it asserts",
    _OS_SYSTEM_PATTERN: "this suite honours the argument-list discipline it asserts",
    _SUBPROCESS_PATTERN: "a unit suite over a command builder starts no process and runs "
    "neither the ported scripts nor a nested test session",
    _pattern("sele", "nium"): "browser automation stays confined to the page objects and the "
    "driver factory",
    _pattern("web", "driver"): "browser automation stays confined to the page objects and the "
    "driver factory",
    _pattern("By", "."): "locator syntax belongs to the page objects, not to a unit suite",
    _pattern("requirements", "-dev"): "the harness manifest is requirements-test.txt",
}


# ---------------------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------------------
def _blanked(source: str, spans: Sequence[tuple[int, int, int, int]]) -> str:
    """Replace every character inside ``spans`` with a space, keeping the line layout.

    Blanking rather than deleting is deliberate: line and column numbers survive, so a
    failing assertion over the projected source still points at the real line of the file.

    Args:
        source: The text to project.
        spans: ``(lineno, col_offset, end_lineno, end_col_offset)`` tuples, with one-based
            line numbers and zero-based columns -- exactly the convention the ``ast`` and
            ``tokenize`` modules use, so spans can be handed over untranslated.

    Returns:
        ``source`` with the spanned characters replaced by spaces.
    """
    lines: list[str] = source.splitlines()
    for lineno, column, end_lineno, end_column in spans:
        if lineno == end_lineno:
            line = lines[lineno - 1]
            lines[lineno - 1] = line[:column] + " " * (end_column - column) + line[end_column:]
            continue
        first = lines[lineno - 1]
        lines[lineno - 1] = first[:column] + " " * (len(first) - column)
        for index in range(lineno, end_lineno - 1):
            lines[index] = " " * len(lines[index])
        last = lines[end_lineno - 1]
        lines[end_lineno - 1] = " " * min(end_column, len(last)) + last[end_column:]
    return "\n".join(lines)


def _code_only_source(source: str) -> str:
    """Project ``source`` down to the text that actually executes.

    Docstrings and comments are blanked out. That distinction is what makes every prohibition
    below an assertion about behaviour rather than about prose: the module under test is
    expected to explain, in words and in doctest examples, the shell semantics it replaces and
    the timeout it deliberately leaves to another layer. Searching its raw text for those
    words would flag the explanation as the offence.

    Args:
        source: Complete module text.

    Returns:
        The same text with every docstring and comment replaced by spaces.
    """
    tree: ast.Module = ast.parse(source)
    spans: list[tuple[int, int, int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if not node.body:
            continue
        leading = node.body[0]
        if not isinstance(leading, ast.Expr) or not isinstance(leading.value, ast.Constant):
            continue
        if not isinstance(leading.value.value, str):
            continue
        docstring = leading.value
        # Every node carries an end position; the guard keeps the type checker honest.
        assert docstring.end_lineno is not None
        assert docstring.end_col_offset is not None
        spans.append(
            (
                docstring.lineno,
                docstring.col_offset,
                docstring.end_lineno,
                docstring.end_col_offset,
            )
        )
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            spans.append((token.start[0], token.start[1], token.end[0], token.end[1]))
    return _blanked(source, spans)


def _import_roots(tree: ast.Module) -> set[str]:
    """Collect the top-level name of every module the tree imports.

    Args:
        tree: Parsed module.

    Returns:
        Root module names, with ``"."`` standing for any relative import so that a relative
        first-party import cannot slip past a name-based check.
    """
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                roots.add(".")
            elif node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _platform_system_calls(node: ast.AST) -> list[ast.Call]:
    """Collect every ``platform.system(...)`` call reachable from ``node``.

    Args:
        node: Any AST node to search, module or subtree.

    Returns:
        The matching call nodes, in traversal order.
    """
    return [
        candidate
        for candidate in ast.walk(node)
        if isinstance(candidate, ast.Call)
        and isinstance(candidate.func, ast.Attribute)
        and candidate.func.attr == "system"
        and isinstance(candidate.func.value, ast.Name)
        and candidate.func.value.id == "platform"
    ]


def _windows_comparisons(tree: ast.Module) -> list[ast.Compare]:
    """Collect every inequality comparison against the literal ``"Windows"``.

    Args:
        tree: Parsed module.

    Returns:
        The matching comparison nodes, in traversal order.
    """
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(isinstance(operator, ast.NotEq) for operator in node.ops)
        and any(
            isinstance(comparator, ast.Constant) and comparator.value == _WINDOWS_PLATFORM_NAME
            for comparator in node.comparators
        )
    ]


def _module_level_nodes(tree: ast.Module) -> list[ast.AST]:
    """Collect every node reachable without entering a function or class body.

    Anything found here runs at import time, which is exactly what the platform probe must
    not do: a value captured into a module-level constant would freeze the branch for the
    lifetime of the process.

    Args:
        tree: Parsed module.

    Returns:
        The import-time nodes.
    """
    collected: list[ast.AST] = []
    for statement in tree.body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        collected.extend(ast.walk(statement))
    return collected


def _assert_absent(pattern: str, source: str, table: Mapping[str, str], *, label: str) -> None:
    """Assert ``pattern`` does not appear in ``source``, explaining why it is banned.

    Shared by every prohibition test so the tables stay the single place a ban is declared,
    and so a failure names the construct, the file and the reason in one message.

    Args:
        pattern: The construct that must not appear.
        source: The text to search -- always a code-only projection for the module under
            test, and the raw text for this suite's own hygiene checks.
        table: The prohibition table ``pattern`` came from, consulted for the reason.
        label: Repository-relative name of the file being searched.
    """
    assert pattern not in source, f"{label} uses {pattern!r}: {table[pattern]}"


def _assert_argv_command(command: object, *, context: str) -> list[str]:
    """Assert ``command`` is an argument list of separate string tokens, and return it.

    This is the shared enforcement of the port's execution contract: the pipeline's ``sh``
    and ``bat`` steps took one command *string* and handed it to a shell, and the Python port
    replaces both with an explicit argument list. A returned ``str`` would still "work" when
    iterated, silently degrading into a sequence of single characters, so the type is checked
    rather than assumed.

    Args:
        command: The value returned by the module under test.
        context: What produced the value, quoted in failure messages.

    Returns:
        The same value, narrowed to ``list[str]``.
    """
    assert isinstance(command, list), (
        f"{context} must return a list of argv tokens so it can be executed with no shell, "
        f"but returned {type(command).__name__}"
    )
    assert command, f"{context} must return at least one token naming the executable"
    for position, token in enumerate(command):
        assert isinstance(
            token, str
        ), f"{context} token {position} must be a str, not {type(token).__name__}"
    return command


# ---------------------------------------------------------------------------------------
# Fixtures.
#
# All three are session-scoped: the file is read and parsed once for the whole run. They are
# derived from the `project_root` fixture in `tests/conftest.py`, which resolves the
# repository root from its own location rather than from the working directory -- the run is
# parallel by default, every worker is a separate process, and none is guaranteed to have
# been started from the root, so a relative path would be a latent flake.
# ---------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def module_path(project_root: Path) -> Path:
    """Return the absolute path of the module under test."""
    return project_root.joinpath(*_MODULE_RELATIVE_PARTS)


@pytest.fixture(scope="session")
def module_source(module_path: Path) -> str:
    """Return the complete text of the module under test."""
    assert module_path.is_file(), f"{_MODULE_LABEL} is missing at {module_path}"
    return module_path.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def module_code(module_source: str) -> str:
    """Return the module's source with docstrings and comments blanked out."""
    return _code_only_source(module_source)


@pytest.fixture(scope="session")
def module_tree(module_source: str) -> ast.Module:
    """Return the parsed syntax tree of the module under test."""
    return ast.parse(module_source)


# ---------------------------------------------------------------------------------------
# The public surface, and the two ported branch payloads.
# ---------------------------------------------------------------------------------------
def test_public_api_is_exactly_the_documented_surface() -> None:
    """The module exports the predicate, the selector, the builder and the two payloads."""
    exported = set(platform_exec.__all__)
    expected = set(_EXPECTED_PUBLIC_API)
    assert exported == expected, f"exported {sorted(exported)}, expected {sorted(expected)}"
    for name in sorted(expected):
        assert hasattr(platform_exec, name), f"{_MODULE_LABEL} declares but does not define {name}"
    private = sorted(name for name in exported if name.startswith("_"))
    assert not private, f"{_MODULE_LABEL} exports private names {private}"


def test_the_posix_payload_ports_the_pipeline_shell_step() -> None:
    """``[Jenkins:L8]`` ``sh "mvn clean test"`` becomes the POSIX script token."""
    payload = platform_exec.POSIX_TEST_COMMAND
    assert isinstance(payload, tuple), (
        "the canonical payload is held as a tuple so it cannot be mutated through a "
        "returned value"
    )
    tokens = _assert_argv_command(list(payload), context="POSIX_TEST_COMMAND")
    assert len(tokens) == 1, "the shell branch ran one command, with no wrapper prepended"
    token = PurePosixPath(tokens[0])
    assert token.name == _POSIX_SCRIPT_NAME
    assert _SCRIPT_DIRECTORY in token.parts
    assert "\\" not in tokens[0], "the POSIX payload uses forward slashes only"


def test_the_windows_payload_ports_the_pipeline_batch_step() -> None:
    """``[Jenkins:L10]`` ``bat "mvn clean test"`` becomes the batch script token."""
    payload = platform_exec.WINDOWS_TEST_COMMAND
    assert isinstance(payload, tuple), (
        "the canonical payload is held as a tuple so it cannot be mutated through a "
        "returned value"
    )
    tokens = _assert_argv_command(list(payload), context="WINDOWS_TEST_COMMAND")
    assert len(tokens) == 1, "the batch branch ran one command, with no wrapper prepended"
    token = PureWindowsPath(tokens[0])
    assert token.name == _WINDOWS_SCRIPT_NAME
    assert _SCRIPT_DIRECTORY in token.parts
    assert "/" not in tokens[0], "the Windows payload uses the native separator only"


def test_the_two_payloads_are_one_command_run_two_ways() -> None:
    """``[Jenkins:L7-L11]``: both branches ran the same command; only the executor differed."""
    posix = PurePosixPath(platform_exec.POSIX_TEST_COMMAND[0])
    windows = PureWindowsPath(platform_exec.WINDOWS_TEST_COMMAND[0])
    assert posix.stem == windows.stem == _SHARED_COMMAND_STEM, (
        "both branches must name the same logical command, exactly as both pipeline branches "
        "ran mvn clean test"
    )
    assert posix.suffix != windows.suffix, (
        "the two branches must differ in executor, exactly as the pipeline's sh step differed "
        "from its bat step"
    )
    assert platform_exec.POSIX_TEST_COMMAND != platform_exec.WINDOWS_TEST_COMMAND


# ---------------------------------------------------------------------------------------
# The predicate: two answers, and no third.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("system", _NON_WINDOWS_PLATFORM_NAMES)
def test_every_non_windows_platform_is_unix(system: str) -> None:
    """``isUnix()`` ``[Jenkins:L7]`` answered true for every platform except Windows."""
    assert platform_exec.is_unix(system=system) is True, (
        f"{system!r} must take the POSIX branch: the ported decision is an exact "
        f'platform.system() != "Windows" comparison, not a normalised one'
    )


def test_only_the_exact_windows_name_is_not_unix() -> None:
    """Only the literal ``"Windows"`` takes the batch branch ``[Jenkins:L10]``."""
    assert platform_exec.is_unix(system=_WINDOWS_PLATFORM_NAME) is False
    corpus = _NON_WINDOWS_PLATFORM_NAMES
    assert _WINDOWS_PLATFORM_NAME not in corpus, "the unix corpus must exclude the Windows name"


def test_the_predicate_answers_with_a_real_boolean() -> None:
    """A binary predicate returns a bool, not a truthy stand-in for one."""
    for system in (_WINDOWS_PLATFORM_NAME, *_NON_WINDOWS_PLATFORM_NAMES):
        answer = platform_exec.is_unix(system=system)
        assert isinstance(answer, bool), (
            f"is_unix({system!r}) returned {type(answer).__name__}; the ported predicate is "
            f"binary and must answer with a bool"
        )


def test_the_default_predicate_answers_for_the_host() -> None:
    """With no argument the host is consulted, as ``isUnix()`` consulted the CI agent."""
    assert platform_exec.is_unix() is (platform.system() != _WINDOWS_PLATFORM_NAME)


def test_the_predicate_rejects_a_platform_name_that_is_not_a_string() -> None:
    """A mistyped override is refused rather than coerced into the wrong branch."""
    with pytest.raises(TypeError):
        platform_exec.is_unix(system=1)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------
# The dispatch, and the seam that makes both branches reachable from one machine.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("system", _NON_WINDOWS_PLATFORM_NAMES)
def test_every_non_windows_platform_gets_the_shell_branch_command(system: str) -> None:
    """``[Jenkins:L8]``: every platform the predicate called unix ran the shell script."""
    command = _assert_argv_command(
        platform_exec.build_test_command(system=system),
        context=f"build_test_command(system={system!r})",
    )
    assert command == list(platform_exec.POSIX_TEST_COMMAND)


def test_windows_gets_the_batch_branch_command() -> None:
    """``[Jenkins:L10]``: the one non-unix platform ran the batch script."""
    command = _assert_argv_command(
        platform_exec.build_test_command(system=_WINDOWS_PLATFORM_NAME),
        context="build_test_command(system='Windows')",
    )
    assert command == list(platform_exec.WINDOWS_TEST_COMMAND)


def test_exactly_two_command_paths_exist_across_every_platform_name() -> None:
    """``isUnix()`` was binary: two branches in, two branches out, and never a third.

    A cross-platform matrix is out of scope (AAP 0.2.2), so this asserts the *absence* of a
    third path over a corpus that deliberately includes macOS, the BSDs, the Windows-hosted
    POSIX layers and case variants of the Windows name itself.
    """
    every_name = (*_NON_WINDOWS_PLATFORM_NAMES, _WINDOWS_PLATFORM_NAME)
    outcomes = {name: tuple(platform_exec.build_test_command(system=name)) for name in every_name}
    assert set(outcomes.values()) == {
        tuple(platform_exec.POSIX_TEST_COMMAND),
        tuple(platform_exec.WINDOWS_TEST_COMMAND),
    }, "the dispatch produced a command that belongs to neither ported branch"
    assert len(set(outcomes.values())) == 2, "a third code path has appeared in the dispatch"
    batch_names = [
        name
        for name, command in outcomes.items()
        if command == tuple(platform_exec.WINDOWS_TEST_COMMAND)
    ]
    assert batch_names == [_WINDOWS_PLATFORM_NAME], (
        f"only the exact name {_WINDOWS_PLATFORM_NAME!r} may take the batch branch, but "
        f"{batch_names} did"
    )


@pytest.mark.parametrize(
    ("override", "expected_payload"),
    [
        (True, "POSIX_TEST_COMMAND"),
        (False, "WINDOWS_TEST_COMMAND"),
    ],
    ids=["is_unix=True", "is_unix=False"],
)
def test_the_boolean_seam_reaches_both_branches_from_one_host(
    override: bool, expected_payload: str
) -> None:
    """The seam is why the batch branch is assertable on a Linux-only CI host at all.

    Both call styles are exercised, because the parameter is the first positional argument as
    well as a keyword: a caller that passes it either way must select the same branch.
    """
    expected = list(getattr(platform_exec, expected_payload))
    assert platform_exec.build_test_command(override) == expected
    assert platform_exec.build_test_command(is_unix=override) == expected


def test_the_boolean_seam_outranks_the_platform_name() -> None:
    """An explicit branch override wins over a supplied platform name and over the host."""
    assert platform_exec.build_test_command(is_unix=True, system=_WINDOWS_PLATFORM_NAME) == list(
        platform_exec.POSIX_TEST_COMMAND
    )
    assert platform_exec.build_test_command(is_unix=False, system="Linux") == list(
        platform_exec.WINDOWS_TEST_COMMAND
    )


def test_the_default_dispatch_follows_the_host_platform() -> None:
    """With no override the host decides, exactly as the CI agent decided ``[Jenkins:L7]``."""
    expected = (
        list(platform_exec.POSIX_TEST_COMMAND)
        if platform.system() != _WINDOWS_PLATFORM_NAME
        else list(platform_exec.WINDOWS_TEST_COMMAND)
    )
    assert (
        _assert_argv_command(platform_exec.build_test_command(), context="build_test_command()")
        == expected
    )


def test_the_host_platform_is_read_at_call_time_not_cached_at_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patching after import must change the answer, proving nothing was frozen at import.

    A module-level constant evaluated while the module loaded would pin the branch for the
    lifetime of the process, and no injected value could ever dislodge it. Patching is used
    here and nowhere else: every other test injects through the explicit seam instead.
    """
    monkeypatch.setattr(platform, "system", lambda: _WINDOWS_PLATFORM_NAME)
    assert platform_exec.is_unix() is False
    assert platform_exec.build_test_command() == list(platform_exec.WINDOWS_TEST_COMMAND)

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert platform_exec.is_unix() is True
    assert platform_exec.build_test_command() == list(platform_exec.POSIX_TEST_COMMAND)


# ---------------------------------------------------------------------------------------
# The Strategy itself: choosing between two supplied command lists.
# ---------------------------------------------------------------------------------------
def test_the_selector_dispatches_between_the_two_supplied_commands() -> None:
    """``select_command`` is the ported ``if(isUnix()){...} else {...}`` ``[Jenkins:L7-L11]``."""
    posix_candidate = ["./first.sh", "--only"]
    windows_candidate = ["second.bat", "--only"]
    assert platform_exec.select_command(posix_candidate, windows_candidate, is_unix=True) == (
        posix_candidate
    )
    assert platform_exec.select_command(posix_candidate, windows_candidate, is_unix=False) == (
        windows_candidate
    )
    assert (
        platform_exec.select_command(
            posix_candidate, windows_candidate, system=_WINDOWS_PLATFORM_NAME
        )
        == windows_candidate
    )
    assert platform_exec.select_command(posix_candidate, windows_candidate, system="Darwin") == (
        posix_candidate
    )


def test_the_selector_refuses_a_joined_command_line() -> None:
    """A single string is the shell command line the port replaces, not an argument list.

    Left unchecked it would iterate into single characters, so the type is rejected outright.
    Both candidates are validated on every call, whichever one is selected -- the only way a
    defect in the branch this host cannot reach can ever surface.
    """
    with pytest.raises(TypeError):
        platform_exec.select_command("make test", ["second.bat"], is_unix=True)
    with pytest.raises(TypeError):
        platform_exec.select_command(["./first.sh"], "make test", is_unix=True)


def test_the_selector_refuses_a_command_that_names_no_executable() -> None:
    """An empty list, or an empty first token, cannot be executed and is refused."""
    with pytest.raises(ValueError, match="at least one token"):
        platform_exec.select_command([], ["second.bat"], is_unix=True)
    with pytest.raises(ValueError, match="must name the executable"):
        platform_exec.select_command([""], ["second.bat"], is_unix=True)


def test_the_selector_refuses_a_token_that_is_not_a_string() -> None:
    """Every argv token must already be a string; nothing is stringified on the way out."""
    with pytest.raises(TypeError):
        platform_exec.select_command([1], ["second.bat"], is_unix=True)  # type: ignore[list-item]


def test_extra_arguments_are_appended_as_separate_tokens() -> None:
    """Additional arguments extend the argument list; they never extend a command string."""
    command = _assert_argv_command(
        platform_exec.build_test_command(is_unix=True, extra_args=["-m", ""]),
        context="build_test_command(extra_args=...)",
    )
    assert command == [
        *platform_exec.POSIX_TEST_COMMAND,
        "-m",
        "",
    ], "an empty argument value is legitimate and must survive as its own token"


def test_extra_arguments_refuse_a_joined_string() -> None:
    """Handing over one string instead of tokens is the mistake this contract prevents."""
    with pytest.raises(TypeError):
        platform_exec.build_test_command(is_unix=True, extra_args="-m LogOut")


def test_a_wrongly_typed_branch_override_is_refused() -> None:
    """A truthy non-boolean would silently select a branch; it raises instead."""
    with pytest.raises(TypeError):
        platform_exec.build_test_command("yes")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        platform_exec.build_test_command(is_unix=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        platform_exec.build_test_command(system=object())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------
# Argument-list discipline: the port of the pipeline's implicit shell semantics.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("branch", [True, False], ids=["posix-branch", "windows-branch"])
def test_each_branch_returns_separate_argv_tokens(branch: bool) -> None:
    """``[Jenkins:L8, L10]``: two shell command strings become two argument lists."""
    command = _assert_argv_command(
        platform_exec.build_test_command(is_unix=branch),
        context=f"build_test_command(is_unix={branch})",
    )
    for position, token in enumerate(command):
        assert token, f"token {position} is empty and names nothing"


@pytest.mark.parametrize("branch", [True, False], ids=["posix-branch", "windows-branch"])
def test_no_token_is_a_compound_command_line(branch: bool) -> None:
    """No token may be a whole command line: there is no shell to split one apart again."""
    for token in platform_exec.build_test_command(is_unix=branch):
        assert not any(
            character.isspace() for character in token
        ), f"{token!r} contains whitespace, so it is a command line rather than one argument"
        for metacharacter in _SHELL_METACHARACTERS:
            assert (
                metacharacter not in token
            ), f"{token!r} contains {metacharacter!r}, which only a shell would interpret"


@pytest.mark.parametrize("branch", [True, False], ids=["posix-branch", "windows-branch"])
def test_no_token_is_pre_quoted_or_shell_escaped(branch: bool) -> None:
    """Quoting an argv token corrupts it: the callee receives the quotes verbatim."""
    for token in platform_exec.build_test_command(is_unix=branch):
        for quote in _QUOTE_CHARACTERS:
            assert not token.startswith(quote), f"{token!r} is pre-quoted"
            assert not token.endswith(quote), f"{token!r} is pre-quoted"
            assert f"\\{quote}" not in token, f"{token!r} carries a shell-escaped quote"
        assert "\\ " not in token, f"{token!r} carries a shell-escaped space"


@pytest.mark.parametrize("branch", [True, False], ids=["posix-branch", "windows-branch"])
def test_every_call_returns_an_independent_list(branch: bool) -> None:
    """Each call builds a fresh list, so no caller can reach shared state through a result."""
    first = platform_exec.build_test_command(is_unix=branch)
    second = platform_exec.build_test_command(is_unix=branch)
    assert first == second
    assert first is not second, "two calls returned the same object"

    canonical = platform_exec.POSIX_TEST_COMMAND if branch else platform_exec.WINDOWS_TEST_COMMAND
    before = tuple(canonical)
    first.append("--mutated")
    assert (
        platform_exec.build_test_command(is_unix=branch) == second
    ), "mutating one result changed a later one, so the branch payload is shared state"
    assert tuple(canonical) == before, "mutating a result reached back into the payload constant"


# ---------------------------------------------------------------------------------------
# Structural discipline, asserted over the module's own source.
# ---------------------------------------------------------------------------------------
def test_the_inspected_file_is_the_imported_module(module_path: Path) -> None:
    """Guard every assertion below: inspect the very file that was imported, not a copy.

    Several checkouts of this repository can sit side by side on one machine, so a path
    composed from the repository root and an interpreter that imported the module from
    somewhere else would silently make every structural assertion meaningless.
    """
    imported = platform_exec.__file__
    assert imported is not None, f"{_MODULE_LABEL} was imported without a file location"
    assert (
        module_path.resolve() == Path(imported).resolve()
    ), f"inspecting {module_path} but the imported module came from {imported}"


def test_the_ported_decision_is_one_exact_platform_comparison(
    module_tree: ast.Module, module_code: str
) -> None:
    """``if(isUnix())`` ``[Jenkins:L7]`` is ported as ``platform.system() != "Windows"``.

    Asserted structurally *and* textually: the syntax tree proves the shape of the decision,
    and the code-only text proves the literal spelling the plan prescribes (AAP 0.3.1) has not
    been paraphrased.
    """
    comparisons = _windows_comparisons(module_tree)
    assert len(comparisons) == 1, (
        f"{_MODULE_LABEL} must decide the branch in exactly one place, found "
        f"{len(comparisons)} inequality comparisons against the Windows name"
    )
    probes = _platform_system_calls(comparisons[0])
    assert len(probes) == 1, "the single decision must be driven by one platform.system() call"
    assert (
        not probes[0].args and not probes[0].keywords
    ), "platform.system() takes no argument; passing one would change what is compared"
    assert "platform.system()" in module_code
    assert f'!= "{_WINDOWS_PLATFORM_NAME}"' in module_code


def test_the_platform_is_probed_only_inside_a_function(module_tree: ast.Module) -> None:
    """No probe may run at import time, or the branch would be frozen for the process."""
    all_probes = _platform_system_calls(module_tree)
    assert (
        len(all_probes) == 1
    ), f"{_MODULE_LABEL} must consult the host in exactly one place, found {len(all_probes)}"
    probes = set(all_probes)
    import_time_probes = [node for node in _module_level_nodes(module_tree) if node in probes]
    assert not import_time_probes, (
        "the host platform is consulted at import time, so an injected or patched value could "
        "never take effect"
    )


@pytest.mark.parametrize("forbidden", sorted(_FORBIDDEN_PLATFORM_PROBE_PATTERNS))
def test_no_alternative_platform_probe_is_used(forbidden: str, module_code: str) -> None:
    """Only the exact comparison the plan prescribes may decide the branch."""
    _assert_absent(forbidden, module_code, _FORBIDDEN_PLATFORM_PROBE_PATTERNS, label=_MODULE_LABEL)


@pytest.mark.parametrize("forbidden", sorted(_FORBIDDEN_EXECUTION_PATTERNS))
def test_the_module_neither_joins_nor_runs_a_command(forbidden: str, module_code: str) -> None:
    """The module builds commands; the service layer executes them, with its own timeout."""
    _assert_absent(forbidden, module_code, _FORBIDDEN_EXECUTION_PATTERNS, label=_MODULE_LABEL)


@pytest.mark.parametrize("forbidden", sorted(_FORBIDDEN_CONFIGURATION_PATTERNS))
def test_the_module_reads_no_configuration(forbidden: str, module_code: str) -> None:
    """Configuration precedence is owned elsewhere; this module resolves one boolean."""
    _assert_absent(forbidden, module_code, _FORBIDDEN_CONFIGURATION_PATTERNS, label=_MODULE_LABEL)


@pytest.mark.parametrize("forbidden", sorted(_FORBIDDEN_FILESYSTEM_PATTERNS))
def test_the_module_touches_no_filesystem(forbidden: str, module_code: str) -> None:
    """Path knowledge and directory creation belong to the artifact-layout module."""
    _assert_absent(forbidden, module_code, _FORBIDDEN_FILESYSTEM_PATTERNS, label=_MODULE_LABEL)


@pytest.mark.parametrize("forbidden", sorted(_FORBIDDEN_EXIT_CODE_PATTERNS))
def test_the_module_implements_no_exit_status_policy(forbidden: str, module_code: str) -> None:
    """Mapping a runner's exit status is the test runner service's decision, not this one."""
    _assert_absent(forbidden, module_code, _FORBIDDEN_EXIT_CODE_PATTERNS, label=_MODULE_LABEL)


@pytest.mark.parametrize("forbidden", sorted(_FORBIDDEN_FRAMEWORK_PATTERNS))
def test_the_module_grows_no_abstraction_framework(forbidden: str, module_code: str) -> None:
    """The dispatch reduces to a predicate and a command builder; that is the right size."""
    _assert_absent(forbidden, module_code, _FORBIDDEN_FRAMEWORK_PATTERNS, label=_MODULE_LABEL)


def test_the_module_defines_no_class(module_tree: ast.Module) -> None:
    """Two branches need no type hierarchy, so none is declared."""
    classes = [node.name for node in ast.walk(module_tree) if isinstance(node, ast.ClassDef)]
    assert not classes, f"{_MODULE_LABEL} declares {classes}, but a two-branch dispatch needs none"


def test_the_module_imports_only_the_standard_library(module_tree: ast.Module) -> None:
    """``app/utils`` is the terminal node of ``api -> services -> reporting -> utils``."""
    roots = _import_roots(module_tree)
    assert roots, f"{_MODULE_LABEL} imports nothing at all, which cannot be right"
    outside_stdlib = sorted(root for root in roots if root not in sys.stdlib_module_names)
    assert not outside_stdlib, (
        f"{_MODULE_LABEL} imports {outside_stdlib}, but a terminal utility module may depend on "
        f"the standard library only"
    )
    assert "." not in roots, "a relative import would reach a sibling first-party module"


@pytest.mark.parametrize("forbidden", sorted(_FORBIDDEN_IMPORT_ROOTS))
def test_the_module_avoids_the_forbidden_import_roots(
    forbidden: str, module_tree: ast.Module
) -> None:
    """Each root below would make a prohibition asserted elsewhere reachable again."""
    assert forbidden not in _import_roots(
        module_tree
    ), f"{_MODULE_LABEL} imports {forbidden!r}: {_FORBIDDEN_IMPORT_ROOTS[forbidden]}"


# ---------------------------------------------------------------------------------------
# This suite holds itself to the discipline it asserts.
#
# The repository's engineering baseline applies to `tests/**` exactly as it applies to `app/`,
# and a suite that asserted argument-list discipline while breaking it would be worthless.
# These checks make that baseline executable rather than a claim in a comment.
# ---------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def suite_path(project_root: Path) -> Path:
    """Return the absolute path of this suite, resolved from the repository root."""
    return project_root.joinpath(*_SUITE_RELATIVE_PARTS)


@pytest.fixture(scope="session")
def suite_source(suite_path: Path) -> str:
    """Return the complete text of this suite.

    Read once, through one call site. Concentrating the read here is what keeps the explicit
    encoding on a single line: a call broken across lines by the formatter would put the read
    on a line of its own and defeat the line-based check below -- which is exactly how this
    fixture came to exist.
    """
    assert suite_path.is_file(), f"this suite is missing at {suite_path}"
    return suite_path.read_text(encoding="utf-8")


def test_this_suite_is_the_file_being_inspected(suite_path: Path) -> None:
    """Compose this file's path the same way, and prove both routes agree."""
    assert suite_path.resolve() == Path(__file__).resolve()


def test_this_suite_is_ascii_only_with_unix_line_endings(suite_path: Path) -> None:
    """Byte-level style contract: pure ASCII, LF only, exactly one trailing newline."""
    data = suite_path.read_bytes()
    assert data, "this suite is empty"
    non_ascii = sorted({byte for byte in data if byte > 0x7F})
    assert not non_ascii, f"non-ASCII bytes present: {non_ascii}"
    assert b"\r" not in data, "carriage returns present; line endings must be LF"
    assert data.endswith(b"\n"), "the file must end with a newline"
    assert not data.endswith(b"\n\n"), "the file must end with exactly one newline"


def test_this_suite_has_no_trailing_whitespace(suite_source: str) -> None:
    """Trailing whitespace produces noisy diffs and is rejected by the formatting gate."""
    offenders = [
        number for number, line in enumerate(suite_source.splitlines(), 1) if line != line.rstrip()
    ]
    assert not offenders, f"trailing whitespace on lines {offenders}"


@pytest.mark.parametrize("forbidden", sorted(_FORBIDDEN_SELF_PATTERNS))
def test_this_suite_avoids_the_constructs_it_forbids(forbidden: str, suite_source: str) -> None:
    """Every pattern here is assembled from fragments, so only a real use would match."""
    _assert_absent(forbidden, suite_source, _FORBIDDEN_SELF_PATTERNS, label=_SUITE_LABEL)


def test_this_suite_reads_every_file_with_an_explicit_encoding(suite_source: str) -> None:
    """Explicit UTF-8 on all text I/O, so nothing depends on the host's default encoding.

    Line-based on purpose: an encoding argument that the formatter has pushed onto a line of
    its own is still correct Python, but it hides the argument from every reviewer and every
    hygiene grep that reads one line at a time. Keeping each read on a single line is the
    property being asserted, not merely the presence of the argument somewhere nearby.
    """
    offenders = [
        number
        for number, line in enumerate(suite_source.splitlines(), 1)
        if (_READ_TEXT_CALL_PATTERN in line or _OPEN_CALL_PATTERN in line)
        and "encoding=" not in line
    ]
    assert not offenders, f"text is read without an explicit encoding on lines {offenders}"
