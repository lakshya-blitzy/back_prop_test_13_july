"""Tests for ``app/utils/paths.py`` - the port's single owner of every path.

AAP 0.4.2: *"Artifact paths are owned by ``app/utils/paths.py``.  Every writer,
the artifact route, the clean step and the per-worker invocation take their
paths from it, and no other Python module contains a path literal."*  Path
drift is how this port breaks silently - a writer that spells its own
``target/cucumber.json`` keeps working until the owner's value changes - so the
ownership claim is worth only what a test makes of it.  This module is that
test, and it is the only one that covers:

* the four artifact paths of the Java plugin list (``CukesRunner.java:9-14``,
  the JSON one also ``Jenkins:15``'s ``fileIncludePattern``) and the
  PrettyReports sub-tree of AAP 0.3.4;
* the per-worker intermediates under ``target/.workers/`` - ``pom.xml:22-23``'s
  ``parallel=methods`` and ``useUnlimitedThreads`` reproduced as a process pool
  by AAP deviation 4 - whose zero-padded shard index makes name order numeric
  order, as AAP 0.6's deterministic merge requires;
* the rejection matrix of :func:`~app.utils.paths.resolve_artifact`, where AAP
  0.3.1's "allowlisted artifact only" and never-reachable ``.workers/`` are
  decided (``tests/test_web_routes.py`` exercises the same rules over HTTP);
* the sole feature-URI normalization helper (AAP deviation 1) every writer test
  compares through, plus the two ``tests/conftest.py`` wrappers' delegation;
* the no-follow artifact I/O surface, whose refusals - a symlink at the end of
  the path or anywhere from ``target`` inward, a hard link, a FIFO, a directory
  where a file belongs, a destination changed between check and open - are
  asserted against real filesystem shapes in Sections 11 and 12.

Conventions: every path assertion drives production code through its ``base=``
seam with :fixture:`tmp_artifact_root`, so nothing here writes into the real
``target/``; every assertion states one settled outcome unconditionally, the
only conditional cases being :data:`SYMLINKS_AVAILABLE`,
:data:`FIFOS_AVAILABLE` and :data:`HARD_LINKS_AVAILABLE`, which are platform
capabilities rather than behaviours under test; a refusal no real filesystem
shape can produce is driven through the owner's own portability seam
(``_NO_FOLLOW_SUPPORTED``, ``_O_NOFOLLOW``, ``_O_DIRECTORY``) or
:func:`os.open`, never by calling a private helper; and because the owner is
the only module permitted to spell a path literal, the literal
``"src/main/resources/features/"`` appears here - the module that proves the
rewrite - and nowhere else under ``tests/``.
"""

from __future__ import annotations

import ast
import contextlib
import errno
import inspect
import io
import os
import pathlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Final

import pytest

import conftest as suite_conftest
from app import utils as utils_package
from app.utils import paths

# --------------------------------------------------------------------------
# Fixed expectations
#
# The values below are spelled out rather than derived from the module under
# test: a test that builds its expectation out of the same constant it is
# checking cannot fail.  Their authority is the Java runner's plugin list at
# ``CukesRunner.java:9-14``, plus AAP 0.3.4 for the PrettyReports sub-tree.
# --------------------------------------------------------------------------

#: ``target`` - the build output directory Maven emptied and ``--clean`` empties.
EXPECTED_TARGET_DIR: Final[str] = "target"

#: ``features`` - the feature directory after AAP deviation 1.
EXPECTED_FEATURES_DIR: Final[str] = "features"

#: ``.workers`` - the per-worker intermediate directory (AAP 0.4.1).
EXPECTED_WORKERS_DIR: Final[str] = ".workers"

#: The PrettyReports plugin's output directory (``CukesRunner.java:13``).
EXPECTED_PRETTY_DIR: Final[str] = "cucumber"

#: Where the generator puts its pages and assets (AAP 0.3.4).
EXPECTED_PRETTY_SUBDIR: Final[str] = "cucumber-html-reports"

#: The PrettyReports overview page (AAP 0.3.1).
EXPECTED_PRETTY_INDEX: Final[str] = "overview-features.html"

#: ``CukesRunner.java:10`` - the single self-contained HTML report.
EXPECTED_HTML_NAME: Final[str] = "cucumber-reports.html"

#: ``CukesRunner.java:11`` - the JSON report, and ``Jenkins:15``'s include
#: pattern.
EXPECTED_JSON_NAME: Final[str] = "cucumber.json"

#: ``CukesRunner.java:12`` - the rerun manifest.
EXPECTED_RERUN_NAME: Final[str] = "rerun.txt"

#: The feature-directory prefix the Java tree used and the golden fixtures
#: carry.  This file is the one place under ``tests/`` permitted to name it,
#: because this file is what proves the rewrite.
EXPECTED_LEGACY_PREFIX: Final[str] = "src/main/resources/features/"

#: The four artifact keys in plugin order (``CukesRunner.java:9-14``).
EXPECTED_ARTIFACT_KEYS: Final[tuple[str, ...]] = (
    EXPECTED_HTML_NAME,
    EXPECTED_JSON_NAME,
    EXPECTED_RERUN_NAME,
    EXPECTED_PRETTY_DIR,
)

#: Path fragments no string constant outside the owner may contain.  Every one
#: of them is a name :mod:`app.utils.paths` owns; the scan that uses them is
#: AST-based and covers docstrings as well as executable strings, because AAP
#: 0.4.2 exempts nothing and duplicated path documentation drifts silently.
OWNED_PATH_FRAGMENTS: Final[tuple[str, ...]] = (
    f"{EXPECTED_TARGET_DIR}/",
    EXPECTED_JSON_NAME,
    EXPECTED_RERUN_NAME,
    EXPECTED_HTML_NAME,
    EXPECTED_WORKERS_DIR,
    EXPECTED_PRETTY_SUBDIR,
    EXPECTED_PRETTY_INDEX,
    "worker-",
)

#: The consumers AAP 0.4.2 names: the four writers, the artifact route, the
#: clean step and the per-worker invocation.  Each must take its paths from the
#: owner.  ``app/reporting/events.py`` is included because it writes the
#: per-worker intermediate file.
REQUIRED_PATH_CONSUMERS: Final[tuple[str, ...]] = (
    "app/reporting/html_report.py",
    "app/reporting/cucumber_json.py",
    "app/reporting/rerun_report.py",
    "app/reporting/pretty_reports.py",
    "app/reporting/events.py",
    "app/web/routes.py",
    "app/cli.py",
    "app/services/test_run_service.py",
)

#: Import roots ``app/utils/paths.py`` may use.  AAP 0.4.2 requires the module
#: to import nothing from the ``app`` package so that a worker process which
#: never builds a Flask application can still resolve its own output path.
#: ``errno``, ``io`` and ``stat`` are on the list because the owner opens and
#: verifies the components of a path itself -- descriptor-relative and
#: ``O_NOFOLLOW`` -- and needs the error numbers, the buffered wrappers and the
#: mode predicates to do it; every one of them is in the standard library,
#: which is the property the test additionally asserts by name so a future
#: addition cannot smuggle a third-party dependency in behind this list.
ALLOWED_OWNER_IMPORT_ROOTS: Final[frozenset[str]] = frozenset(
    {
        "collections",
        "contextlib",
        "errno",
        "io",
        "os",
        "pathlib",
        "stat",
        "typing",
    }
)

#: Heavyweight modules importing the owner must not drag in, asserted in a
#: subprocess so the result cannot depend on what another test module imported
#: first.
FORBIDDEN_TRANSITIVE_IMPORTS: Final[tuple[str, ...]] = (
    "flask",
    "selenium",
    "behave",
    "click",
    "jinja2",
)

#: Calls that remove a filesystem entry.  Confined by the module docstring to
#: the publication scratch helpers: emptying ``target/`` belongs to
#: ``app/cli.py`` and the per-worker directory lifecycle to
#: ``app/services/test_run_service.py`` (AAP 0.4.1).
FORBIDDEN_OWNER_CALLS: Final[frozenset[str]] = frozenset(
    {"rmtree", "unlink", "rmdir", "remove", "removedirs"}
)

#: The only owner definitions permitted to contain one of those calls.  The
#: first is the descriptor-relative, no-follow scratch removal; the second is
#: the fallback branch's disposal, which removes only what needs no descent and
#: renames the rest aside; the third removes the temporary of a failed
#: :func:`publish_artifact_file`; and the class is where a publication's own
#: staging and renamed-aside trees are discarded - each of which refuses a name
#: that is not its own scratch.
ALLOWED_OWNER_REMOVAL_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        "_remove_tree_relative",
        "_detach_tree_by_name",
        "_discard_temporary",
        "ArtifactDirectoryPublication",
    }
)


# --------------------------------------------------------------------------
# Hostile path input
#
# ``ArtifactSpec`` is a plain NamedTuple, so any caller can build one; the
# owner therefore does not trust a supplied spec to describe its own location
# and looks it up by its own ``key``, requiring equality with the canonical
# member of ``ARTIFACT_SPECS`` (``app/utils/paths.py``, ``artifact_path``).
# The relpaths below are the four shapes that would otherwise escape - two
# traversals, one traversal behind a legitimate first component, and one
# absolute path - and each is asserted to be rejected outright.
# --------------------------------------------------------------------------

#: Relative paths a caller-supplied :class:`~app.utils.paths.ArtifactSpec`
#: must not be able to reach, whether by traversal or by absolute path.
HOSTILE_SPEC_RELPATHS: Final[tuple[str, ...]] = (
    "../../etc/passwd",
    "../escaped.json",
    "target/../../escaped.json",
    "/etc/passwd",
)


def _within(candidate: Path, root: Path) -> bool:
    """Whether ``candidate`` lands inside ``root`` once both are resolved.

    Resolution is what makes the question meaningful: ``base/../../etc/passwd``
    is lexically below ``base`` and is not below it at all.  A path that cannot
    be resolved counts as outside, because a containment claim that cannot be
    checked is not a containment claim.

    :param candidate: The path under scrutiny.
    :param root: The directory it must not escape.
    :returns: ``True`` only when containment is established.
    """
    try:
        return candidate.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):  # pragma: no cover - platform-dependent
        return False


#: Whether this platform can create a symbolic link.  The escape test needs one
#: and there is no way to fake it; a platform without symlinks cannot be
#: escaped through one either, so skipping is the honest outcome.
SYMLINKS_AVAILABLE: Final[bool] = hasattr(os, "symlink")

#: Whether this platform can create a FIFO.  The owner refuses one because
#: opening it for writing would block a writer for ever and reading it would
#: hand a consumer bytes no run produced; where :func:`os.mkfifo` is absent -
#: Windows - no artifact path can be occupied by one.
FIFOS_AVAILABLE: Final[bool] = hasattr(os, "mkfifo")

#: Whether this platform can create a hard link.  The owner refuses an entry
#: with more than one link because it *is* a file elsewhere; where
#: :func:`os.link` is absent that entry cannot be prepared.
HARD_LINKS_AVAILABLE: Final[bool] = hasattr(os, "link")

#: A file outside the artifact root that a hostile link or hard link points at,
#: shared by Sections 7, 11 and 12.  Its content is asserted intact after every
#: refusal: a check that runs after the destination has been overwritten is not
#: a check.
VICTIM_NAME: Final[str] = "victim.json"

#: Content of that file, distinctive enough that finding it anywhere else - or
#: finding it gone - is unambiguous.
VICTIM_CONTENT: Final[str] = '{"kept": true}'


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _owner_source() -> str:
    """Return the source text of ``app/utils/paths.py``.

    Read through the imported module's own ``__file__`` rather than through a
    path built here, so the file inspected is provably the file imported.

    :returns: The owner module's source.
    """
    return Path(paths.__file__).read_text(encoding="utf-8")


def _parse(path: Path) -> ast.Module:
    """Parse one Python file, failing with its name if it does not parse.

    :param path: File to parse.
    :returns: Its abstract syntax tree.
    """
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # pragma: no cover - only on a broken tree
        pytest.fail(f"{path} does not parse: {exc}")


def _is_symbol_citation(value: str, start: int, end: int) -> bool:
    """Whether ``value[start:end]`` sits inside a dotted symbol reference.

    ``app/utils/paths.py`` owns every path, so a sibling module refers to one
    **by the symbol that owns it** - ``app.utils.paths.workers_dir``,
    ``paths.cucumber_json_path`` - and asks that function for the value at run
    time.  Some owned names are substrings of those symbols (``.workers`` of
    ``.workers_dir``), so a plain substring search reports the citation as if
    it were the duplicated literal it is the precise opposite of.

    The occurrence's *shape* decides.  The match is grown over the characters a
    dotted Python name is made of; if what results is a longer chain of two or
    more identifier segments, it is a reference to a symbol and not a path.  A
    genuine literal never grows: it is bounded by a quote, a space or a path
    separator, so it comes back equal to the fragment and is reported.

    :param value: The string constant the fragment was found in.
    :param start: Start offset of the occurrence.
    :param end: End offset of the occurrence.
    :returns: ``True`` when the occurrence is part of a dotted symbol.
    """
    name_characters = "_."
    left = start
    while left > 0 and (value[left - 1].isalnum() or value[left - 1] in name_characters):
        left -= 1
    right = end
    while right < len(value) and (value[right].isalnum() or value[right] in name_characters):
        right += 1

    token = value[left:right]
    if token == value[start:end]:
        return False
    segments = token.strip(".").split(".")
    return len(segments) >= 2 and all(segment.isidentifier() for segment in segments)


def _spells_owned_path(value: str, fragment: str) -> bool:
    """Whether ``value`` spells ``fragment`` as a path rather than as prose.

    Two shapes are not the duplicated literal AAP 0.4.2 forbids, and counting
    them would push the cleanup towards *removing* the symbolic references the
    invariant exists to encourage:

    * a dotted symbol citation - see :func:`_is_symbol_citation`;
    * an English compound built on the per-worker file's prefix
      (``worker-local``, ``worker-controlled``, ``worker-intermediates``).  The
      owner's own name is ``worker-<pid>-<shard>.json``, so a real literal
      continues with a digit or with a placeholder for one, and a compound
      continues with a letter.

    :param value: The string constant to examine.
    :param fragment: The owned fragment to look for.
    :returns: ``True`` when at least one occurrence is a path literal.
    """
    start = value.find(fragment)
    while start != -1:
        end = start + len(fragment)
        tail = value[end : end + 1]
        compound = fragment.endswith("-") and (tail.isalpha() or tail == "")
        if not compound and not _is_symbol_citation(value, start, end):
            return True
        start = value.find(fragment, start + 1)
    return False


def _string_constants(tree: ast.Module) -> list[tuple[int, str]]:
    """Every string constant of a tree, docstrings included.

    AAP 0.4.2 admits no exemption - *"no other Python module contains a path
    literal"* - and documentation is exactly where a duplicated path drifts
    unnoticed, because prose is not executed and so is never contradicted by a
    test.  Nothing is filtered out here for that reason: module, class and
    function docstrings are returned alongside executable strings.

    f-string fragments are included too, because ``f"{root}/target/x"`` carries
    its literal in a :class:`ast.Constant` exactly like a plain string does.

    :param tree: A parsed module.
    :returns: ``(line number, value)`` pairs in traversal order.
    """
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _python_sources(repo_root: Path) -> list[Path]:
    """Every Python module of the application and the Gherkin glue.

    ``tests/`` is out of scope: this suite is not bound by the
    no-path-literal rule - it has to name the values it pins - and
    ``scripts/`` holds no Python.

    :param repo_root: The repository root.
    :returns: Existing ``.py`` files under ``app/`` and ``features/``, sorted.
    """
    found = list((repo_root / "app").rglob("*.py"))
    found += list((repo_root / "features").rglob("*.py"))
    return sorted(path for path in found if path.is_file())


def _owner_imports(tree: ast.Module) -> list[tuple[str, tuple[str, ...]]]:
    """The names a module imports from the path owner or the ``app.utils`` barrel.

    :param tree: A parsed module.
    :returns: ``(module, names)`` pairs for each such import statement.
    """
    collected: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "app.utils",
            "app.utils.paths",
        }:
            collected.append((node.module, tuple(alias.name for alias in node.names)))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"app.utils", "app.utils.paths"}:
                    collected.append((alias.name, ()))
    return collected


def _tree_entries(root: Path) -> tuple[str, ...]:
    """A sorted snapshot of everything under ``root``, relative to it.

    Used to assert that an accessor created nothing, which is the property
    :fixture:`tmp_artifact_root` exists to make checkable.

    :param root: Directory to snapshot.
    :returns: Relative POSIX paths of every entry beneath ``root``.
    """
    return tuple(sorted(entry.relative_to(root).as_posix() for entry in root.rglob("*")))


def _write(path: Path, content: str = "x") -> Path:
    """Create ``path`` and its parents with known content.

    The parent directory is made through :func:`app.utils.paths.ensure_parent`,
    the port's own helper, rather than through ``mkdir`` - the same choice
    :fixture:`prepared_artifact_root` makes in ``tests/conftest.py``, so a
    fixture cannot prepare a tree by a route production code does not use.

    :param path: File to create.
    :param content: Text to write; the default is enough for an existence check.
    :returns: The file's path.
    """
    paths.ensure_parent(path).write_text(content, encoding="utf-8")
    return path


#: The working-directory-relative accessors, each with the components it must
#: append to ``base``.  Spelled as literals on purpose - see the note above
#: :data:`EXPECTED_TARGET_DIR`.
ACCESSOR_EXPECTATIONS: Final[
    tuple[tuple[str, Callable[..., Path], tuple[str, ...]], ...]
] = (
    ("target_root", paths.target_root, ("target",)),
    ("features_dir", paths.features_dir, ("features",)),
    (
        "cucumber_reports_html_path",
        paths.cucumber_reports_html_path,
        ("target", "cucumber-reports.html"),
    ),
    ("cucumber_json_path", paths.cucumber_json_path, ("target", "cucumber.json")),
    ("rerun_txt_path", paths.rerun_txt_path, ("target", "rerun.txt")),
    ("pretty_reports_dir", paths.pretty_reports_dir, ("target", "cucumber")),
    (
        "pretty_reports_html_dir",
        paths.pretty_reports_html_dir,
        ("target", "cucumber", "cucumber-html-reports"),
    ),
    (
        "pretty_reports_index_path",
        paths.pretty_reports_index_path,
        ("target", "cucumber", "cucumber-html-reports", "overview-features.html"),
    ),
    ("workers_dir", paths.workers_dir, ("target", ".workers")),
)


# ==========================================================================
# Section 1 - Ownership and the published surface
#
# Contract: AAP 0.4.2 - one owner for every path, a barrel that re-exports it
# and contains no logic, an import boundary that keeps the owner usable in a
# worker process, and no deletion helper anywhere in it.
# ==========================================================================


def test_all_entries_resolve_and_are_unique() -> None:
    """``paths.__all__`` is a duplicate-free list of names the module defines.

    A barrel is documentation of a surface (AAP 0.4.2), and an entry naming
    something that does not exist turns the import in ``app/utils/__init__.py``
    into a runtime error rather than a contract.
    """
    exported = list(paths.__all__)
    assert len(exported) == len(set(exported)), "paths.__all__ repeats a name"
    missing = [name for name in exported if not hasattr(paths, name)]
    assert missing == [], f"paths.__all__ names undefined attributes: {missing}"


def test_all_matches_the_modules_own_public_definitions() -> None:
    """Everything the owner defines publicly is exported, and nothing else is.

    The AAP makes this module the single source of truth for paths, so a public
    helper left out of ``__all__`` is a path accessor that consumers cannot
    reach through the package, and an exported name the module does not define
    itself would make the barrel re-export somebody else's API.  Definitions are
    read from the source tree rather than from ``vars()``, which cannot tell a
    definition from an import.
    """
    tree = ast.parse(_owner_source())
    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, ast.Assign):
            defined.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
    public = {name for name in defined if not name.startswith("_")}
    assert public == set(paths.__all__), (
        "paths.__all__ and the module's public definitions disagree: "
        f"unexported={sorted(public - set(paths.__all__))}, "
        f"undefined={sorted(set(paths.__all__) - public)}"
    )


@pytest.mark.parametrize("name", sorted(paths.__all__))
def test_barrel_reexports_each_owner_name_as_the_same_object(name: str) -> None:
    """``app.utils`` re-exports every owner name, identically.

    ``app/utils/__init__.py`` states that every name it imports is re-exported
    and appears in its own ``__all__``.  Identity - not equality - is the
    assertion: a barrel that rebound a name to a copy would let the package and
    the module disagree about what, say, ``ARTIFACT_SPECS`` is.
    """
    assert hasattr(utils_package, name), f"app.utils does not re-export {name}"
    assert getattr(utils_package, name) is getattr(paths, name)
    assert name in utils_package.__all__, f"{name} is missing from app.utils.__all__"


def test_owner_imports_only_the_standard_library() -> None:
    """The owner imports nothing from ``app`` and nothing heavy (AAP 0.4.2).

    *"``app/utils`` imports nothing from the package, so ``paths.py`` is
    importable in a worker that never builds a Flask application."*  The check
    is on the import statements themselves, so it holds whatever the process
    that runs it has already imported.
    """
    tree = ast.parse(_owner_source())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "the owner must not use a relative import"
            assert node.module is not None
            roots.add(node.module.split(".")[0])
    assert roots <= ALLOWED_OWNER_IMPORT_ROOTS, (
        f"app/utils/paths.py imports unexpected roots: "
        f"{sorted(roots - ALLOWED_OWNER_IMPORT_ROOTS)}"
    )
    # The allowlist is a list of names; this is the rule behind it, so a name
    # added to that list still has to be part of the standard library and
    # cannot be ``app`` or a third-party package.
    assert roots <= set(sys.stdlib_module_names), (
        f"app/utils/paths.py imports outside the standard library: "
        f"{sorted(roots - set(sys.stdlib_module_names))}"
    )


def test_importing_the_owner_pulls_in_no_framework() -> None:
    """Importing the owner leaves Flask, selenium, behave, Click and Jinja2 out.

    AAP 0.4.2's import boundary exists so a shard worker can resolve its own
    output path without a web framework being dragged into the process.  Run in
    a subprocess because the assertion is about a fresh interpreter: by the time
    this module runs, the suite has imported Flask for the route tests, and an
    in-process ``sys.modules`` check would report that instead.
    """
    script = (
        "import sys\n"
        "import app.utils.paths\n"
        "loaded = [name for name in "
        f"{list(FORBIDDEN_TRANSITIVE_IMPORTS)!r} if name in sys.modules]\n"
        "print(','.join(loaded))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path(paths.__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "", (
        f"importing app.utils.paths loaded: {completed.stdout.strip()}"
    )


def _owner_functions_performing(calls: frozenset[str]) -> dict[str, list[str]]:
    """Map each owner function that calls one of ``calls`` to the calls it makes.

    Attribute and bare calls are both counted, and a call inside a nested
    function or a method is attributed to the enclosing top-level definition,
    which is the unit the deletion boundary is stated in.

    :param calls: Call names to look for, such as ``{"unlink", "rmdir"}``.
    :returns: Top-level function or class name to the calls found inside it.
    """
    tree = ast.parse(_owner_source())
    found: dict[str, list[str]] = {}
    for top in tree.body:
        if not isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(top):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else ""
            )
            if called in calls:
                found.setdefault(top.name, []).append(f"{called} at line {node.lineno}")
    return found


def test_owner_removes_only_its_own_publication_scratch() -> None:
    """Removal is confined to the publication helpers (module docstring).

    *"The only thing this module ever removes is scratch it created itself ...
    no published artifact, no ``target/`` and no ``target/.workers/``."*  The
    fourth artifact is a directory, so it can only be published by building a
    staging tree and swapping it in, and the cleanup of that scratch has to run
    under the same verified descriptors as the rest of the publication - which
    is why the deletion boundary is *where* rather than *whether*.  AAP 0.4.1's
    allocation is unchanged by it: emptying ``target/`` stays with
    ``app/cli.py`` and the per-worker directory lifecycle with
    ``app/services/test_run_service.py``.

    Two halves are checked: no exported name advertises a general-purpose
    removal, and every removal call in the file sits in one of the three
    private helpers that implement scratch cleanup.
    """
    advertised = [
        name
        for name in paths.__all__
        for verb in ("clean", "delete", "purge", "rmtree")
        if verb in name.lower()
    ]
    assert advertised == [], f"the owner advertises a deletion helper: {advertised}"

    performed = _owner_functions_performing(FORBIDDEN_OWNER_CALLS)
    assert set(performed) <= ALLOWED_OWNER_REMOVAL_FUNCTIONS, (
        "the owner removes filesystem entries outside its publication "
        f"helpers: { {k: v for k, v in performed.items() if k not in ALLOWED_OWNER_REMOVAL_FUNCTIONS} }"
    )
    assert "rmtree" not in _owner_source(), (
        "the owner must not delegate a removal to shutil.rmtree, which follows "
        "no rule about what it descends"
    )


def test_owner_holds_no_module_level_mutable_state() -> None:
    """Accessors are safe under a process pool (module docstring, AAP 0.4.1).

    *"The current directory is read on every call and never cached at import
    time, and the module holds no mutable state, so the accessors are safe under
    a process pool."*  A pool forks this module into every worker, so a
    module-level list or set would be state one worker could mutate for the
    others without any of them synchronising: the artifact table is a tuple, the
    allowlist a frozenset, and the private spec index a mapping built once at
    import and only ever read.
    """
    mutable = [
        name
        for name, value in vars(paths).items()
        if not name.startswith("__") and isinstance(value, (list, set, bytearray))
    ]
    assert mutable == [], f"the owner holds mutable module-level state: {mutable}"
    assert isinstance(paths.ARTIFACT_SPECS, tuple)


# ==========================================================================
# Section 2 - Name components and the POSIX relative-path strings
#
# Contract: the plugin list at CukesRunner.java:9-14, Jenkins:15 for the JSON
# include pattern, AAP 0.3.4 for the PrettyReports sub-tree.
# ==========================================================================


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        ("TARGET_DIR_NAME", EXPECTED_TARGET_DIR),
        ("FEATURES_DIR_NAME", EXPECTED_FEATURES_DIR),
        ("WORKERS_DIR_NAME", EXPECTED_WORKERS_DIR),
        ("PRETTY_REPORTS_DIR_NAME", EXPECTED_PRETTY_DIR),
        ("PRETTY_HTML_SUBDIR", EXPECTED_PRETTY_SUBDIR),
        ("PRETTY_OVERVIEW_INDEX", EXPECTED_PRETTY_INDEX),
        ("CUCUMBER_REPORTS_HTML_NAME", EXPECTED_HTML_NAME),
        ("CUCUMBER_JSON_NAME", EXPECTED_JSON_NAME),
        ("RERUN_TXT_NAME", EXPECTED_RERUN_NAME),
        ("FILE_URI_SCHEME", "file:"),
        ("LEGACY_FEATURES_PREFIX", EXPECTED_LEGACY_PREFIX),
        ("NORMALIZED_FEATURES_PREFIX", "features/"),
    ],
    ids=lambda value: value if isinstance(value, str) else repr(value),
)
def test_name_component_has_its_source_value(attribute: str, expected: str) -> None:
    """Each name component equals the value its authority fixes.

    The three artifact file names and the PrettyReports directory come from the
    plugin list at ``CukesRunner.java:9-14``; the
    PrettyReports sub-directory and overview page from AAP 0.3.4; the feature
    directory and its two prefixes from AAP deviation 1.  These are a
    compatibility surface, not an internal spelling, so each is pinned.
    """
    assert getattr(paths, attribute) == expected


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        ("CUCUMBER_REPORTS_HTML_RELPATH", f"{EXPECTED_TARGET_DIR}/{EXPECTED_HTML_NAME}"),
        ("CUCUMBER_JSON_RELPATH", f"{EXPECTED_TARGET_DIR}/{EXPECTED_JSON_NAME}"),
        ("RERUN_TXT_RELPATH", f"{EXPECTED_TARGET_DIR}/{EXPECTED_RERUN_NAME}"),
        ("PRETTY_REPORTS_RELPATH", f"{EXPECTED_TARGET_DIR}/{EXPECTED_PRETTY_DIR}"),
    ],
    ids=["html", "json", "rerun", "pretty"],
)
def test_relpath_string_is_posix_and_matches_the_plugin_list(
    attribute: str, expected: str
) -> None:
    """The four relative-path strings render with forward slashes everywhere.

    They are strings rather than paths because ``target/cucumber.json`` is the
    exact value of the Jenkins publisher's ``fileIncludePattern`` (``Jenkins:15``)
    and all four are quoted in the plugin list of ``CukesRunner.java:9-14`` and
    named in the README's artifact table.  A backslash here would break both
    the publisher glob and the document.
    """
    value = getattr(paths, attribute)
    assert value == expected
    assert "\\" not in value, "a relative-path string must never carry a backslash"
    assert not value.startswith("/"), "the relative paths are relative"


def test_workers_directory_is_dot_prefixed() -> None:
    """``.workers`` is dot-prefixed, which is what hides it from the route.

    AAP 0.3.1 requires ``.workers/`` to be unreachable through
    ``GET /artifacts/<path:name>``, and :func:`resolve_artifact` implements that
    by rejecting every dot-prefixed component rather than by naming this
    directory - so the leading dot is load-bearing, not cosmetic.
    """
    assert paths.WORKERS_DIR_NAME.startswith(".")
    assert paths.WORKERS_DIR_NAME == EXPECTED_WORKERS_DIR


def test_feature_prefixes_are_directory_prefixes() -> None:
    """Both feature prefixes end in a separator (docstring of the constants).

    *"The trailing slash is deliberate, so removing it yields a bare
    filename."*  The normalized prefix is derived from the feature directory
    name, so the two cannot drift apart.
    """
    assert paths.LEGACY_FEATURES_PREFIX.endswith("/")
    assert paths.NORMALIZED_FEATURES_PREFIX == f"{paths.FEATURES_DIR_NAME}/"
    assert paths.FILE_URI_SCHEME.endswith(":")


# ==========================================================================
# Section 3 - Working-directory-relative accessors and the ``base`` seam
#
# Contract: the owner's "Conventions" docstring - ``base`` last, defaulting to
# the working directory, read on every call, never cached; no filesystem
# access; ``base`` is the only override mechanism (AAP 0.4.1: no environment
# layer).
# ==========================================================================


@pytest.mark.parametrize(
    ("name", "accessor", "components"),
    ACCESSOR_EXPECTATIONS,
    ids=[name for name, _accessor, _components in ACCESSOR_EXPECTATIONS],
)
def test_accessor_appends_its_components_to_the_supplied_base(
    name: str,
    accessor: Callable[..., Path],
    components: tuple[str, ...],
    tmp_artifact_root: Path,
) -> None:
    """Each accessor resolves to exactly the documented path under ``base``.

    The expectation is spelled out component by component rather than rebuilt
    from the constants the accessor itself uses, so a mis-wired accessor - the
    JSON path pointing at the rerun name, say - fails here instead of agreeing
    with itself.  Authority: ``CukesRunner.java:9-14`` and AAP 0.3.4.
    """
    assert accessor(tmp_artifact_root) == tmp_artifact_root.joinpath(*components)
    assert name in paths.__all__


@pytest.mark.parametrize(
    ("name", "accessor", "components"),
    ACCESSOR_EXPECTATIONS,
    ids=[name for name, _accessor, _components in ACCESSOR_EXPECTATIONS],
)
def test_accessor_accepts_a_string_base(
    name: str,
    accessor: Callable[..., Path],
    components: tuple[str, ...],
    tmp_artifact_root: Path,
) -> None:
    """``base`` may be a string, as its ``Path | str | None`` annotation says.

    The seam exists so a caller that has a path as text - a CLI argument, a
    worker's environment - need not convert it first.
    """
    assert accessor(str(tmp_artifact_root)) == tmp_artifact_root.joinpath(*components)
    assert isinstance(accessor(str(tmp_artifact_root)), Path)


def test_accessors_touch_the_filesystem_for_nothing(tmp_artifact_root: Path) -> None:
    """No accessor creates anything (docstrings: *"the directory is not created"*).

    :fixture:`tmp_artifact_root` hands over a checkout root with ``target/``
    deliberately absent, which is what makes the claim checkable: after calling
    every accessor the root is still empty, so a writer test that asserts its
    writer created ``target/`` is testing the writer.
    """
    before = _tree_entries(tmp_artifact_root)
    for _name, accessor, _components in ACCESSOR_EXPECTATIONS:
        accessor(tmp_artifact_root)
    paths.worker_result_path(0, pid=4321, base=tmp_artifact_root)
    paths.artifact_path(paths.CUCUMBER_JSON_NAME, base=tmp_artifact_root)
    assert before == ()
    assert _tree_entries(tmp_artifact_root) == ()
    assert not paths.target_root(tmp_artifact_root).exists()


def test_base_defaults_to_the_working_directory(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting ``base`` resolves against the process working directory.

    This is how Maven resolved ``target/`` and how the Java
    ``ConfigurationReader`` opened a bare relative filename, which is why the
    port keeps the behaviour (owner docstring, "Conventions").
    """
    monkeypatch.chdir(tmp_artifact_root)
    assert paths.target_root() == Path.cwd() / EXPECTED_TARGET_DIR
    assert paths.cucumber_json_path() == (
        Path.cwd() / EXPECTED_TARGET_DIR / EXPECTED_JSON_NAME
    )
    assert paths.features_dir() == Path.cwd() / EXPECTED_FEATURES_DIR


def test_working_directory_is_read_on_every_call(
    tmp_artifact_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The working directory is never cached at import time (AAP 0.4.1).

    *"The current directory is read on every call and never cached at import
    time ... so the accessors are safe under a process pool."*  A worker that
    changes directory has to see the change, so the same accessor is called from
    two directories in one process and must answer differently.
    """
    second = tmp_path / "second-checkout"
    second.mkdir()

    monkeypatch.chdir(tmp_artifact_root)
    first_answer = paths.target_root()
    monkeypatch.chdir(second)
    second_answer = paths.target_root()

    assert first_answer != second_answer
    assert first_answer == tmp_artifact_root / EXPECTED_TARGET_DIR
    assert second_answer == second / EXPECTED_TARGET_DIR


def test_base_is_the_last_parameter_of_every_accessor() -> None:
    """``base`` comes last everywhere, as the owner's conventions require.

    *"Every working-directory-relative accessor takes ``base`` as its last
    parameter and defaults to :meth:`pathlib.Path.cwd`."*  Consumers rely on
    that uniformity - ``worker_result_path(index, base=root)`` reads the same way
    as ``cucumber_json_path(root)`` - and a signature that broke it would be a
    silent API divergence rather than an error.
    """
    accessors = [accessor for _name, accessor, _components in ACCESSOR_EXPECTATIONS]
    accessors += [paths.artifact_path, paths.worker_result_path]
    accessors += [paths.iter_worker_result_paths, paths.resolve_artifact]
    for accessor in accessors:
        parameters = list(inspect.signature(accessor).parameters)
        assert parameters[-1] == "base", f"{accessor.__name__} must take base last"
        assert (
            inspect.signature(accessor).parameters["base"].default is None
        ), f"{accessor.__name__}'s base must default to None"


def test_the_owner_offers_no_environment_override() -> None:
    """``base`` is the only override mechanism (AAP 0.4.1).

    The AAP forbids an environment layer for this port's configuration, and the
    owner's conventions repeat it: *"``base`` is the only override mechanism -
    no environment layer is added - and it exists so tests can inject a
    temporary directory."*  So the module must not read the environment at all.
    """
    source_tree = ast.parse(_owner_source())
    offenders = [
        node.lineno
        for node in ast.walk(source_tree)
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}
    ]
    assert offenders == [], f"the owner reads the environment at lines {offenders}"


# ==========================================================================
# Section 4 - Package-relative locators
#
# Contract: the owner's docstring - these four take no ``base``, are derived
# from ``__file__`` and are therefore unaffected by the working directory of
# whichever process asks.  They locate package data declared in pyproject.toml.
# ==========================================================================


def test_package_root_is_the_app_package_directory(repo_root: Path) -> None:
    """``package_root()`` is ``app/``, not the repository root (docstring).

    *"Note this is the installed package directory, **not** the repository root
    and not the working directory."*  In this checkout the two coincide one
    level apart, which is exactly why the distinction needs pinning.
    """
    assert paths.package_root() == repo_root / "app"
    assert paths.package_root().is_dir()
    assert paths.package_root().is_absolute()
    assert paths.package_root() == paths.package_root().resolve()


@pytest.mark.parametrize(
    ("name", "locator", "components"),
    [
        ("templates_dir", paths.templates_dir, ("templates",)),
        ("static_dir", paths.static_dir, ("static",)),
        ("vendor_dir", paths.vendor_dir, ("static", "vendor")),
    ],
    ids=["templates", "static", "vendor"],
)
def test_package_locator_points_inside_the_package(
    name: str, locator: Callable[[], Path], components: tuple[str, ...]
) -> None:
    """Each locator points at real package data under ``app/``.

    ``app/templates`` holds the artifact template sets, the view templates, the
    shared partials and the error pages (AAP 0.3.1); ``app/static`` holds the
    stylesheet and script ``html_report.py`` inlines; ``app/static/vendor``
    holds the assets ``pretty_reports.py`` copies into the emitted tree so those
    pages render offline from a CI workspace (AAP 0.3.4).  All three are
    declared as package data, so all three must exist.
    """
    assert locator() == paths.package_root().joinpath(*components)
    assert locator().is_dir(), f"{name} does not exist in this checkout"
    assert name in paths.__all__


@pytest.mark.parametrize(
    "locator",
    [paths.package_root, paths.templates_dir, paths.static_dir, paths.vendor_dir],
    ids=["package_root", "templates_dir", "static_dir", "vendor_dir"],
)
def test_package_locator_takes_no_base_and_ignores_the_working_directory(
    locator: Callable[[], Path],
    tmp_artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The four locators are package-relative, not working-directory-relative.

    *"They are derived from ``__file__`` so they stay correct in a worker
    process running from any directory."*  This is the distinction the owner
    calls "most likely to be misused", so both halves are asserted: no ``base``
    parameter exists, and the answer does not move when the process does.
    """
    assert list(inspect.signature(locator).parameters) == []
    before = locator()
    monkeypatch.chdir(tmp_artifact_root)
    assert locator() == before
    assert locator().is_absolute()


# ==========================================================================
# Section 5 - The four artifacts: ARTIFACT_SPECS and artifact_path
#
# Contract: the four plugin targets of CukesRunner.java:9-14, in plugin order;
# the keys double as the allowlisted names of GET /artifacts/<path:name> (AAP
# 0.3.1); an unknown key - or a hand-built ArtifactSpec that is not one of the
# four canonical members - is a programming error and is raised loudly.
# ==========================================================================


def test_artifact_specs_are_the_four_plugin_targets_in_order() -> None:
    """The spec list reproduces the plugin list of ``CukesRunner.java:9-14``.

    Order is part of the contract - *"The ``GET /`` route lists them in this
    order"* - and so is the html, json, rerun, pretty sequence of
    ``CukesRunner.java:9-14``.  The keys are the final path components of the
    four targets, which is what lets them double as the HTTP allowlist.
    """
    assert isinstance(paths.ARTIFACT_SPECS, tuple)
    assert len(paths.ARTIFACT_SPECS) == 4
    assert tuple(spec.key for spec in paths.ARTIFACT_SPECS) == EXPECTED_ARTIFACT_KEYS
    assert tuple(spec.relpath for spec in paths.ARTIFACT_SPECS) == (
        paths.CUCUMBER_REPORTS_HTML_RELPATH,
        paths.CUCUMBER_JSON_RELPATH,
        paths.RERUN_TXT_RELPATH,
        paths.PRETTY_REPORTS_RELPATH,
    )
    assert len({spec.key for spec in paths.ARTIFACT_SPECS}) == 4


def test_only_the_pretty_reports_artifact_is_a_directory() -> None:
    """``is_dir`` is ``True`` for the PrettyReports tree and nothing else.

    *"``True`` only for the PrettyReports tree; the other three artifacts are
    single files."*  The flag drives the ``GET /`` listing and the artifact
    route's directory handling, so a wrong value would make the route serve a
    directory or refuse a file.
    """
    directories = [spec.key for spec in paths.ARTIFACT_SPECS if spec.is_dir]
    assert directories == [EXPECTED_PRETTY_DIR]


@pytest.mark.parametrize(
    "spec", paths.ARTIFACT_SPECS, ids=[spec.key for spec in paths.ARTIFACT_SPECS]
)
def test_each_spec_key_is_the_last_component_of_its_relpath(
    spec: paths.ArtifactSpec,
) -> None:
    """Each key is the final component of its relative path (docstring).

    That identity is why the same four strings can serve as artifact keys and as
    the allowlisted request names of AAP 0.3.1; if they diverged, the route's
    allowlist would name something ``artifact_path`` could not resolve.
    """
    assert spec.relpath.split("/")[-1] == spec.key
    assert spec.relpath.startswith(f"{EXPECTED_TARGET_DIR}/")


def test_artifact_spec_is_an_immutable_three_field_record() -> None:
    """``ArtifactSpec`` is a :class:`typing.NamedTuple` of key, relpath, is_dir.

    Immutability matters because :data:`ARTIFACT_SPECS` is module-level state
    the ``GET /`` route iterates: a mutable record would let one request's
    handler rewrite the artifact table for every later one.
    """
    assert paths.ArtifactSpec._fields == ("key", "relpath", "is_dir")
    assert issubclass(paths.ArtifactSpec, tuple)
    spec = paths.ARTIFACT_SPECS[0]
    with pytest.raises(AttributeError):
        spec.key = "rewritten"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("key", "accessor"),
    [
        (EXPECTED_HTML_NAME, paths.cucumber_reports_html_path),
        (EXPECTED_JSON_NAME, paths.cucumber_json_path),
        (EXPECTED_RERUN_NAME, paths.rerun_txt_path),
        (EXPECTED_PRETTY_DIR, paths.pretty_reports_dir),
    ],
    ids=list(EXPECTED_ARTIFACT_KEYS),
)
def test_artifact_path_by_key_agrees_with_the_dedicated_accessor(
    key: str, accessor: Callable[..., Path], tmp_artifact_root: Path
) -> None:
    """Keyed lookup and the named accessor are the same path.

    The ``GET /`` route iterates :data:`ARTIFACT_SPECS` and asks
    :func:`artifact_path` for each presence and modification time, while the
    writers use the named accessors.  If the two disagreed, the index would
    report an artifact as absent that had just been written.  For the
    ``cucumber`` key the answer is the *directory*, matching ``is_dir``.
    """
    assert paths.artifact_path(key, base=tmp_artifact_root) == accessor(tmp_artifact_root)


@pytest.mark.parametrize(
    "spec", paths.ARTIFACT_SPECS, ids=[spec.key for spec in paths.ARTIFACT_SPECS]
)
def test_artifact_path_accepts_a_canonical_spec(
    spec: paths.ArtifactSpec, tmp_artifact_root: Path
) -> None:
    """A canonical spec resolves exactly as its key does.

    The four members of :data:`ARTIFACT_SPECS` are the only legitimate spec
    input - the owner looks a supplied spec up by its own ``key`` and requires
    equality with the canonical member - so this is the half of that rule which
    must keep working: the ``GET /`` route passes whole specs while the writers
    pass keys, and both have to land on the same path.
    """
    assert paths.artifact_path(spec, base=tmp_artifact_root) == paths.artifact_path(
        spec.key, base=tmp_artifact_root
    )
    assert paths.artifact_path(spec, base=tmp_artifact_root) == (
        tmp_artifact_root.joinpath(*spec.relpath.split("/"))
    )


def test_artifact_path_rejects_an_unknown_key_loudly(tmp_artifact_root: Path) -> None:
    """An unknown key raises ``KeyError`` naming it (docstring, "Raises").

    *"Unlike :func:`resolve_artifact`, which validates untrusted request input
    and returns ``None``, this function is called with a key chosen in code, so
    an unknown key is a programming error and is raised loudly."*  The message
    has to carry the offending key and the valid set, because the caller that
    sees it is a developer.
    """
    with pytest.raises(KeyError) as failure:
        paths.artifact_path("cucumber.xml", base=tmp_artifact_root)
    message = str(failure.value)
    assert "cucumber.xml" in message
    for key in EXPECTED_ARTIFACT_KEYS:
        assert key in message


def test_artifact_path_defaults_to_the_working_directory(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``artifact_path`` follows the same ``base`` convention as the accessors."""
    monkeypatch.chdir(tmp_artifact_root)
    assert paths.artifact_path(EXPECTED_JSON_NAME) == Path.cwd().joinpath(
        EXPECTED_TARGET_DIR, EXPECTED_JSON_NAME
    )
    assert paths.artifact_path(EXPECTED_JSON_NAME, base=str(tmp_artifact_root)) == (
        tmp_artifact_root / EXPECTED_TARGET_DIR / EXPECTED_JSON_NAME
    )


def test_artifact_path_cannot_be_driven_outside_the_artifact_root(
    tmp_artifact_root: Path,
) -> None:
    """A hostile spec is rejected outright - one outcome, for every shape.

    AAP 0.4.2 makes ``app/utils/paths.py`` the single source of artifact paths,
    so path construction must not be steerable by its caller.  A supplied
    :class:`~app.utils.paths.ArtifactSpec` is looked up by its own ``key`` and
    required to equal the canonical member of :data:`ARTIFACT_SPECS`, which
    makes every one of :data:`HOSTILE_SPEC_RELPATHS` a ``KeyError``: no path is
    built from caller text at all, whether the escape is a traversal, a
    traversal behind a legitimate first component, or an absolute path.

    The message is asserted too, in both directions: it names the four
    canonical keys, because the caller that sees it is a developer, and it
    never echoes the supplied ``relpath``, which would reflect attacker-chosen
    text into a log line for no diagnostic gain.  The containment check inside
    the ``raises`` block is the guard against the other possible regression: if
    a later change answered instead of raising, that assertion fails the case
    rather than letting an escaping path pass as an answer.
    """
    for relpath in HOSTILE_SPEC_RELPATHS:
        spec = paths.ArtifactSpec(key="hostile", relpath=relpath, is_dir=False)

        with pytest.raises(KeyError) as failure:
            answered = paths.artifact_path(spec, base=tmp_artifact_root)
            # Unreachable while the owner rejects.  An ``AssertionError`` is not
            # a ``KeyError``, so if this line is ever reached with an escaping
            # path the case fails here instead of passing inside ``raises``.
            assert _within(answered, paths.target_root(tmp_artifact_root)), (
                f"{relpath!r} resolved outside the artifact root: {answered}"
            )

        message = str(failure.value)
        for key in EXPECTED_ARTIFACT_KEYS:
            assert key in message
        assert relpath not in message, f"the rejection echoed {relpath!r}"


def test_artifact_path_rejects_a_spec_whose_key_cannot_even_be_looked_up(
    tmp_artifact_root: Path,
) -> None:
    """An unhashable ``key`` on a hand-built spec is the same programming error.

    :class:`~app.utils.paths.ArtifactSpec` is a plain
    :class:`~typing.NamedTuple`, so a caller can put anything in it, and a
    mutable ``key`` makes the canonical lookup itself raise
    :exc:`TypeError` rather than answer.  The owner's docstring commits to one
    outcome for every spec that is not one of the four canonical members -
    *"KeyError: ... or is an :class:`ArtifactSpec` that is not one of the four
    canonical specs"* - so the lookup failure is folded into that answer
    instead of reaching the caller as a second, unrelated exception type it
    would have to handle separately.
    """
    unhashable = paths.ArtifactSpec(
        key=[EXPECTED_JSON_NAME],
        relpath=f"{EXPECTED_TARGET_DIR}/{EXPECTED_JSON_NAME}",
        is_dir=False,
    )

    with pytest.raises(KeyError) as failure:
        paths.artifact_path(unhashable, base=tmp_artifact_root)
    assert not isinstance(failure.value, TypeError)
    assert all(key in str(failure.value) for key in EXPECTED_ARTIFACT_KEYS)


def test_artifact_path_refuses_a_spec_table_that_escapes_the_artifact_root(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The containment invariant is asserted by the owner, not assumed by callers.

    ``artifact_path`` checks its own result against :func:`target_root` even for
    a spec it looked up itself, *"because this function is the single source of
    the paths every writer and the artifact route open (AAP 0.4.2), so its
    containment invariant is asserted here rather than assumed by each of
    them"*.  No canonical member can reach that branch - all four relpaths begin
    with the build-output directory - so the only way to establish that the
    check is live is to make the table inconsistent, which is done through the
    owner's own private spec index: the code reads it, and
    :meth:`~pytest.MonkeyPatch.setitem` restores it.  A test that instead
    trusted the branch would be trusting a comment.
    """
    escaping_key = "inconsistent"
    monkeypatch.setitem(
        paths._SPECS_BY_KEY,
        escaping_key,
        paths.ArtifactSpec(
            key=escaping_key,
            relpath=f"{os.pardir}/escaped{Path(EXPECTED_JSON_NAME).suffix}",
            is_dir=False,
        ),
    )

    with pytest.raises(KeyError) as failure:
        paths.artifact_path(escaping_key, base=tmp_artifact_root)
    assert escaping_key in str(failure.value)
    assert _tree_entries(tmp_artifact_root) == ()


# ==========================================================================
# Section 6 - Per-worker intermediates
#
# Contract: pom.xml:22-23 (parallel=methods, useUnlimitedThreads) reproduced as
# a process pool by AAP deviation 4; the file name is unique by both pid and
# shard index; the zero-padded index makes name order numeric order, which
# AAP 0.6 needs for a deterministic merge.  iter_worker_result_paths reads only
# a missing or non-directory worker directory as absence and propagates every
# other OSError, which is asserted below.
# ==========================================================================


def test_workers_dir_sits_beneath_target(tmp_artifact_root: Path) -> None:
    """``target/.workers`` - beneath ``target/``, which is why Jenkins narrowed.

    The directory has to live under the build output - *"which is what makes the
    narrowed Jenkins glob necessary"* - while staying invisible to the artifact
    route, and ``app/cli.py`` removes it before the command returns so no
    intermediate JSON is ever visible to the publisher (AAP 0.4.1).
    """
    assert paths.workers_dir(tmp_artifact_root) == (
        paths.target_root(tmp_artifact_root) / EXPECTED_WORKERS_DIR
    )
    assert paths.workers_dir(tmp_artifact_root).parent == paths.target_root(
        tmp_artifact_root
    )
    assert not paths.workers_dir(tmp_artifact_root).exists()


@pytest.mark.parametrize(
    ("shard_index", "expected_field"),
    [
        (0, "0000"),
        (1, "0001"),
        (7, "0007"),
        (42, "0042"),
        (999, "0999"),
        (1000, "1000"),
        (12345, "12345"),
    ],
    ids=["0", "1", "7", "42", "999", "1000", "12345"],
)
def test_worker_result_name_zero_pads_the_shard_index(
    shard_index: int, expected_field: str, tmp_artifact_root: Path
) -> None:
    """The name is ``worker-<pid>-<shard>.json`` with a four-digit shard.

    *"It is zero-padded in the filename so lexicographic order - the order
    :func:`iter_worker_result_paths` returns - matches numeric order."*  An
    index wider than the padding is not truncated, because a truncated name
    would collide with another shard's.
    """
    path = paths.worker_result_path(shard_index, pid=4242, base=tmp_artifact_root)
    assert path.name == f"worker-4242-{expected_field}.json"
    assert path.parent == paths.workers_dir(tmp_artifact_root)
    assert not path.exists()


def test_worker_result_path_defaults_to_the_calling_process(
    tmp_artifact_root: Path,
) -> None:
    """An omitted ``pid`` is the calling process's own (docstring).

    A worker naming its own output file supplies no pid; a parent naming a
    child's supplies one.  Both halves of the name are load-bearing: a pid alone
    collides when one process handles several shards, an index alone collides
    across processes (``pom.xml:22-23``).
    """
    default_named = paths.worker_result_path(3, base=tmp_artifact_root)
    explicit = paths.worker_result_path(3, pid=os.getpid(), base=tmp_artifact_root)
    assert default_named == explicit
    assert str(os.getpid()) in default_named.name


def test_worker_result_names_are_unique_across_pids_and_shards(
    tmp_artifact_root: Path,
) -> None:
    """One name per (pid, shard) pair, with no collisions (docstring).

    The uniqueness claim is the reason the merge step can trust the directory
    listing, so it is asserted over a small grid rather than a single pair.
    """
    names = {
        paths.worker_result_path(shard, pid=pid, base=tmp_artifact_root).name
        for pid in (11, 12)
        for shard in (0, 1, 2)
    }
    assert len(names) == 6


def test_iter_worker_result_paths_orders_by_shard_index(
    tmp_artifact_root: Path,
) -> None:
    """Listing order is numeric shard order within one pid (AAP 0.6).

    *"The merge step ... must not depend on filesystem iteration order, because
    AAP 0.6 requires the merged structure to be deterministic for a fixed set of
    shard inputs."*  The files are created in a deliberately scrambled order and
    the indices straddle the padding width, which is exactly where an unpadded
    name would sort ``10`` before ``2``.
    """
    indices = [11, 2, 0, 100, 10, 1]
    for shard in indices:
        _write(paths.worker_result_path(shard, pid=777, base=tmp_artifact_root), "{}")

    listed = paths.iter_worker_result_paths(tmp_artifact_root)

    assert isinstance(listed, tuple)
    assert [path.name for path in listed] == [
        paths.worker_result_path(shard, pid=777, base=tmp_artifact_root).name
        for shard in sorted(indices)
    ]


def test_iter_worker_result_paths_sorts_by_name_across_pids(
    tmp_artifact_root: Path,
) -> None:
    """Every existing result file is returned, ordered by name.

    Across pids the order is lexicographic by construction - there is no
    meaningful numeric order between two processes - and what matters is that it
    is *stable*, so two merges of the same directory produce the same document.
    """
    for pid in (900, 1000):
        for shard in (0, 1):
            _write(paths.worker_result_path(shard, pid=pid, base=tmp_artifact_root), "{}")

    listed = paths.iter_worker_result_paths(tmp_artifact_root)

    assert len(listed) == 4
    assert [path.name for path in listed] == sorted(path.name for path in listed)
    assert paths.iter_worker_result_paths(tmp_artifact_root) == listed
    for path in listed:
        assert path.parent == paths.workers_dir(tmp_artifact_root)


def test_iter_worker_result_paths_ignores_everything_else(
    tmp_artifact_root: Path,
) -> None:
    """Only ``worker-*.json`` *files* are returned.

    The directory is the port's own scratch space, but the merge step must not
    be confused by an editor's leftover, a partially renamed file or a
    directory that happens to match the pattern - hence the prefix, suffix and
    ``is_file()`` filter the implementation applies.
    """
    workers = paths.ensure_dir(paths.workers_dir(tmp_artifact_root))
    kept = _write(paths.worker_result_path(0, pid=5, base=tmp_artifact_root), "{}")
    _write(workers / "worker-5-0001.json.tmp", "{}")
    _write(workers / "worker-5-0002.txt", "{}")
    _write(workers / "results.json", "{}")
    _write(workers / "orker-5-0003.json", "{}")
    paths.ensure_dir(workers / "worker-5-0004.json")

    assert paths.iter_worker_result_paths(tmp_artifact_root) == (kept,)


def test_iter_worker_result_paths_treats_absence_as_no_results(
    tmp_artifact_root: Path,
) -> None:
    """An absent or empty directory yields ``()`` and is never an error.

    *"A missing directory simply means no worker has written yet and is never an
    error."*  Both states occur legitimately: before the run service creates the
    directory, and after ``--clean`` has emptied it.
    """
    assert paths.iter_worker_result_paths(tmp_artifact_root) == ()

    paths.ensure_dir(paths.workers_dir(tmp_artifact_root))
    assert paths.iter_worker_result_paths(tmp_artifact_root) == ()


def test_iter_worker_result_paths_tolerates_a_non_directory(
    tmp_artifact_root: Path,
) -> None:
    """A file where the directory should be yields ``()``.

    ``NotADirectoryError`` is the second benign absence: the port's own path is
    occupied by something that cannot hold results, which means there are none.
    It is asserted separately from the missing case because the settled contract
    names exactly these two errors as suppressible.
    """
    paths.ensure_dir(paths.target_root(tmp_artifact_root))
    paths.workers_dir(tmp_artifact_root).write_text("not a directory", encoding="utf-8")

    assert paths.iter_worker_result_paths(tmp_artifact_root) == ()


def test_iter_worker_result_paths_suppresses_a_missing_directory_error(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``FileNotFoundError`` from the listing itself is suppressed.

    The directory can disappear between the existence check and the listing -
    ``app/cli.py`` removes it after the merge - so the benign-absence tolerance
    has to cover the error as well as the state.  Injected rather than raced
    for, because a race is not a test.
    """
    paths.ensure_dir(paths.workers_dir(tmp_artifact_root))

    def _vanished(_self: Path) -> Any:
        raise FileNotFoundError(errno.ENOENT, "the directory was removed")

    monkeypatch.setattr(pathlib.Path, "iterdir", _vanished)
    assert paths.iter_worker_result_paths(tmp_artifact_root) == ()


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can dangle",
)
def test_iter_worker_result_paths_skips_an_entry_whose_target_is_gone(
    tmp_artifact_root: Path,
) -> None:
    """A correctly named entry that resolves to nothing is an absence too.

    *"An entry that disappears mid-scan, or whose symlink target is gone, is
    skipped the same way - it is an absence, not a failure."*  The owner stats
    each candidate rather than calling ``is_file()``, and follows links while
    doing it, so a result file reached through a link still counts and one whose
    target has been removed is passed over instead of failing the merge.  The
    surviving sibling in the same directory is what makes this a skip rather
    than a silent empty answer.
    """
    kept = _write(paths.worker_result_path(0, pid=71, base=tmp_artifact_root), "{}")
    dangling = paths.worker_result_path(1, pid=71, base=tmp_artifact_root)
    dangling.symlink_to(paths.worker_result_path(9, pid=71, base=tmp_artifact_root))

    assert dangling.is_symlink()
    assert paths.iter_worker_result_paths(tmp_artifact_root) == (kept,)


# Only a missing or non-directory worker directory is absence.  A blanket
# ``except OSError`` would turn a permission or I/O failure into "no results",
# hiding completed shards and the real infrastructure error behind a success.
def test_iter_worker_result_paths_surfaces_a_real_io_failure(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-benign ``OSError`` reaches the caller - unconditionally.

    The owner returns ``()`` for ``FileNotFoundError`` and
    ``NotADirectoryError`` and propagates every other ``OSError`` subclass, so
    that ``app/services/test_run_service.py`` can name the directory failure
    and apply its non-zero exit class (AAP 0.4.1) instead of merging an empty
    result set.  A shard whose results exist but cannot be read must not be
    reported as a shard that produced none.  The failure is injected by
    patching :meth:`pathlib.Path.iterdir`, because this suite runs as root,
    where a ``chmod 000`` directory is still listable.  The assertion carries
    no probe, no skip and no ``xfail``: a later widening of that handler has to
    fail here rather than quietly disable the case.
    """
    _write(paths.worker_result_path(0, pid=31, base=tmp_artifact_root), "{}")

    def _denied(_self: Path) -> Any:
        raise PermissionError(errno.EACCES, "permission denied")

    monkeypatch.setattr(pathlib.Path, "iterdir", _denied)

    with pytest.raises(PermissionError) as failure:
        paths.iter_worker_result_paths(tmp_artifact_root)
    assert failure.value.errno == errno.EACCES


# ==========================================================================
# Section 7 - Directory creation
#
# Contract: the owner creates directories and removes nothing but the scratch
# its own publication API created; a writer that cannot create its output
# directory must fail the run per the exit contract in AAP 0.4.1 rather than
# carry on silently.
# ==========================================================================


def test_ensure_dir_creates_missing_parents_and_returns_the_directory(
    tmp_artifact_root: Path,
) -> None:
    """``ensure_dir`` creates the whole chain and hands the path back.

    The PrettyReports output tree is three levels below the checkout root, so
    ``parents=True`` is not an optimisation: it is how
    ``target/cucumber/cucumber-html-reports/`` comes into being (AAP 0.3.4).
    """
    target = paths.pretty_reports_html_dir(tmp_artifact_root)
    returned = paths.ensure_dir(target)

    assert returned == target
    assert isinstance(returned, Path)
    assert target.is_dir()
    assert paths.target_root(tmp_artifact_root).is_dir()


def test_ensure_dir_is_idempotent_and_keeps_existing_content(
    tmp_artifact_root: Path,
) -> None:
    """A second call is a no-op that destroys nothing (docstring).

    *"Idempotent: an existing directory is left untouched."*  This is the half
    of the contract that makes ``ensure_dir`` safe to call from every writer on
    every run, and the half a careless ``exist_ok=False`` or a "clean first"
    convenience would break.
    """
    directory = paths.ensure_dir(paths.workers_dir(tmp_artifact_root))
    resident = _write(directory / "worker-1-0000.json", '{"kept": true}')

    assert paths.ensure_dir(directory) == directory
    assert paths.ensure_dir(str(directory)) == directory
    assert resident.read_text(encoding="utf-8") == '{"kept": true}'
    assert _tree_entries(directory) == ("worker-1-0000.json",)


@pytest.mark.parametrize(
    ("shape", "expected"),
    [("occupied", FileExistsError), ("under-a-file", OSError)],
    ids=["path-is-a-file", "parent-is-a-file"],
)
def test_ensure_dir_raises_when_it_cannot_create(
    shape: str, expected: type[OSError], tmp_artifact_root: Path
) -> None:
    """Creation failures are raised, never swallowed (docstring, AAP 0.4.1).

    *"The failure is deliberately not swallowed: a writer that cannot create its
    output directory must fail the run per the exit contract in AAP 0.4.1, not
    carry on silently."*  Both failures are ``OSError`` subclasses, which is the
    type the docstring commits to, and the type is what this asserts: the owner
    creates and verifies each component of the path in turn, so a file standing
    where a parent should be is reported as that component's own refusal
    (``FileExistsError``) rather than as ``ENOTDIR`` on the child, and pinning
    the subclass there would pin an implementation detail of the walk instead of
    the contract.
    """
    occupied = _write(tmp_artifact_root / "occupied", "file, not a directory")
    candidate = occupied if shape == "occupied" else occupied / "child"

    with pytest.raises(expected) as failure:
        paths.ensure_dir(candidate)
    assert isinstance(failure.value, OSError)


def test_ensure_parent_creates_only_the_parent(tmp_artifact_root: Path) -> None:
    """``ensure_parent`` prepares the directory and leaves the file alone.

    Each of the four writers calls it immediately before opening its output, *"so
    that a writer never fails merely because ``target/`` was emptied by the
    ``--clean`` step"*.  Creating the file itself would be wrong: an empty
    ``cucumber.json`` is an artifact the Jenkins publisher would try to read.
    """
    artifact = paths.cucumber_json_path(tmp_artifact_root)
    returned = paths.ensure_parent(artifact)

    assert returned == artifact
    assert artifact.parent.is_dir()
    assert not artifact.exists()


@pytest.mark.parametrize(
    ("name", "accessor"),
    [
        (EXPECTED_HTML_NAME, paths.cucumber_reports_html_path),
        (EXPECTED_JSON_NAME, paths.cucumber_json_path),
        (EXPECTED_RERUN_NAME, paths.rerun_txt_path),
        (EXPECTED_PRETTY_INDEX, paths.pretty_reports_index_path),
    ],
    ids=["html", "json", "rerun", "pretty-index"],
)
def test_ensure_parent_prepares_every_artifact_location(
    name: str, accessor: Callable[..., Path], tmp_artifact_root: Path
) -> None:
    """Every artifact can be written straight after ``ensure_parent``.

    The call is meant to wrap the expression -
    ``ensure_parent(cucumber_json_path()).write_text(...)`` - so the returned
    path is asserted to be immediately writable, which is the property the
    writers actually depend on.
    """
    artifact = paths.ensure_parent(accessor(tmp_artifact_root))
    artifact.write_text("written", encoding="utf-8")

    assert artifact.name == name
    assert artifact.read_text(encoding="utf-8") == "written"
    assert paths.ensure_parent(artifact) == artifact


def test_ensure_parent_accepts_a_string_and_raises_on_an_occupied_parent(
    tmp_artifact_root: Path,
) -> None:
    """``ensure_parent`` takes text, and reports a parent it cannot create.

    Same rationale as :func:`ensure_dir`: the writers' exit contract depends on
    a failure being visible, so an occupied parent is an error rather than a
    silently skipped directory.
    """
    artifact = paths.cucumber_json_path(tmp_artifact_root)
    assert paths.ensure_parent(str(artifact)) == artifact

    occupied = _write(tmp_artifact_root / "occupied", "file, not a directory")
    with pytest.raises(OSError):
        paths.ensure_parent(occupied / "report.json")


def test_ensure_dir_accepts_a_path_with_no_component_to_verify(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path naming no entry is returned, created ordinarily and not refused.

    The trusted-anchor rule has a degenerate case the owner names explicitly:
    *"The components tuple is empty only for a path that names no entry at all -
    a bare filesystem root, or ``.``"*, and for it *"the ordinary create is
    both correct and a no-op"*.  It matters because ``ensure_dir`` is what every
    writer calls on a directory it was handed, including the arbitrary output
    directory ``write_pretty_reports`` accepts, so the current directory has to
    come back unchanged rather than raise.  Asserted from inside a temporary
    directory, and the tree is snapshotted to prove nothing was created.
    """
    monkeypatch.chdir(tmp_artifact_root)
    here = Path(os.curdir)

    returned = paths.ensure_dir(here)

    assert returned == here
    assert returned.is_dir()
    assert _tree_entries(tmp_artifact_root) == ()


def test_ensure_parent_refuses_a_path_that_names_no_entry(
    tmp_artifact_root: Path,
) -> None:
    """A path with nothing to open beneath a directory is refused, not created.

    ``ensure_parent`` is asked for the parent of an *entry*, so the degenerate
    path :func:`ensure_dir` tolerates is a programming error here: the owner
    raises *"an artifact path must name an entry beneath a directory"* rather
    than inventing a final component or silently preparing the filesystem root.
    The refusal is an :exc:`~app.utils.paths.ArtifactPathError`, hence an
    :exc:`OSError`, so a writer that already handles one needs no change.
    """
    root_of_the_filesystem = Path(tmp_artifact_root.anchor)

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.ensure_parent(root_of_the_filesystem)
    assert isinstance(failure.value, OSError)


def test_ensure_dir_refuses_a_parent_directory_component(
    tmp_artifact_root: Path,
) -> None:
    """``..`` inside the artifact tree is refused rather than walked.

    *"``.`` names the directory already held open and ``..`` names one above
    it, so neither can be checked no-follow and neither ever appears in a path
    this module builds."*  A component that cannot be verified as itself is the
    one hole a descending walk would leave, so it is refused outright - which
    also means no caller can climb out of the artifact root by handing the owner
    a relative segment.
    """
    climbing = paths.target_root(tmp_artifact_root) / os.pardir / EXPECTED_PRETTY_DIR

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.ensure_dir(climbing)
    assert os.pardir in str(failure.value)
    assert not (tmp_artifact_root / EXPECTED_PRETTY_DIR).exists()


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be traversed",
)
def test_ensure_dir_refuses_a_symlinked_owned_component(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """A link standing in for ``target/`` is refused, not created through.

    This is the state a ``--no-clean`` run, or a clean that failed part-way,
    can leave behind: the build-output directory exists as a symbolic link to
    somewhere else entirely.  The owner creates *and then opens* each owned
    component under its verified parent, so the ``mkdir`` that finds something
    already there ``lstat``s it and refuses a link instead of creating the
    PrettyReports tree inside the link's target.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    paths.target_root(tmp_artifact_root).symlink_to(outside, target_is_directory=True)

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.ensure_dir(paths.pretty_reports_html_dir(tmp_artifact_root))
    assert EXPECTED_TARGET_DIR in str(failure.value)
    assert _tree_entries(outside) == ()


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be written through",
)
def test_ensure_parent_refuses_a_symlinked_destination(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """A destination that is already a link is refused (docstring).

    *"``ensure_parent`` also refuses a final entry that already exists as a
    symbolic link - the check that protects a writer which then opens the
    returned path itself."*  Without it the writer would open the link's
    target, which is outside the artifact root, and the run would report having
    written an artifact it did not write.  The link's target is asserted
    untouched, because a refusal that happens after the damage is not a
    refusal.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    victim = _write(outside / VICTIM_NAME, VICTIM_CONTENT)

    artifact = paths.cucumber_json_path(tmp_artifact_root)
    paths.ensure_dir(artifact.parent)
    artifact.symlink_to(victim)

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.ensure_parent(artifact)
    assert EXPECTED_JSON_NAME in str(failure.value)
    assert victim.read_text(encoding="utf-8") == VICTIM_CONTENT


@pytest.mark.skipif(
    not HARD_LINKS_AVAILABLE,
    reason="this platform cannot create hard links, so no entry can carry two",
)
def test_ensure_parent_refuses_a_hard_linked_destination(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """A destination with a second hard link is refused (docstring).

    The refusal no symlink check can make: *"a **hard** link - a regular file
    whose link count exceeds one - because the entry *is* that other file, so
    writing the artifact would overwrite it and reading the artifact would
    disclose it, with no symlink anywhere in the path for a symlink check to
    find."*  A run that empties the build output first never meets this case;
    a ``--no-clean`` run over a prepared tree does.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    victim = _write(outside / VICTIM_NAME, VICTIM_CONTENT)

    artifact = paths.cucumber_json_path(tmp_artifact_root)
    paths.ensure_dir(artifact.parent)
    os.link(victim, artifact)

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.ensure_parent(artifact)
    assert EXPECTED_JSON_NAME in str(failure.value)
    assert artifact.stat().st_nlink == 2
    assert victim.read_text(encoding="utf-8") == VICTIM_CONTENT


# ==========================================================================
# Section 8 - Untrusted-input resolution for the artifact route
#
# Contract: AAP 0.3.1 - "an allowlisted artifact only: cucumber-reports.html,
# cucumber.json, rerun.txt, or any path under cucumber/.  A request naming the
# cucumber/ directory serves cucumber-html-reports/overview-features.html.
# Everything else is 404 - any other directory, any absent file, any path
# resolving outside the artifact root, and .workers/ in particular, which must
# never be reachable."
#
# tests/test_web_routes.py exercises this over HTTP; the decision itself is
# asserted here, which is where it is made.
# ==========================================================================


#: A populated PrettyReports asset, so a nested success case is a real file.
PRETTY_ASSET_COMPONENTS: Final[tuple[str, ...]] = ("css", "bootstrap.min.css")

#: A populated detail page, named the way the generator names one.
PRETTY_PAGE_NAME: Final[str] = "report-feature_1.html"


@pytest.fixture
def populated_artifact_root(tmp_artifact_root: Path) -> Path:
    """A checkout root holding all four artifacts and one worker intermediate.

    Every allowlisted name is only reachable when the file behind it exists, so
    the rejection matrix would pass vacuously against an empty tree: a hostile
    name would be refused for absence rather than for being hostile.  The
    directory is therefore fully populated - including
    ``target/.workers/worker-4242-0000.json``, so that "``.workers/`` must never
    be reachable" is asserted against a file that is genuinely there.

    :param tmp_artifact_root: The empty checkout root.
    :returns: The same root, populated through the owner's own helpers.
    """
    _write(paths.cucumber_reports_html_path(tmp_artifact_root), "<html>report</html>")
    _write(paths.cucumber_json_path(tmp_artifact_root), "[]")
    _write(paths.rerun_txt_path(tmp_artifact_root), "")
    _write(paths.pretty_reports_index_path(tmp_artifact_root), "<html>overview</html>")
    _write(
        paths.pretty_reports_html_dir(tmp_artifact_root) / PRETTY_PAGE_NAME,
        "<html>feature</html>",
    )
    _write(
        paths.pretty_reports_html_dir(tmp_artifact_root).joinpath(
            *PRETTY_ASSET_COMPONENTS
        ),
        "body{}",
    )
    _write(paths.worker_result_path(0, pid=4242, base=tmp_artifact_root), "{}")
    return tmp_artifact_root


@pytest.mark.parametrize(
    ("name", "accessor"),
    [
        (EXPECTED_HTML_NAME, paths.cucumber_reports_html_path),
        (EXPECTED_JSON_NAME, paths.cucumber_json_path),
        (EXPECTED_RERUN_NAME, paths.rerun_txt_path),
    ],
    ids=["html", "json", "rerun"],
)
def test_resolve_artifact_serves_each_allowlisted_file(
    name: str, accessor: Callable[..., Path], populated_artifact_root: Path
) -> None:
    """The three file keys resolve to the paths their accessors name.

    The returned value is the *unresolved* candidate, built from
    :func:`target_root` exactly as the other accessors build theirs - *"so it
    compares equal to, say, :func:`cucumber_json_path`"* - which is what lets
    ``app/web/routes.py`` hand it to Flask's sender without a second spelling of
    the path.
    """
    resolved = paths.resolve_artifact(name, base=populated_artifact_root)

    assert resolved == accessor(populated_artifact_root)
    assert resolved is not None, f"{name} was rejected although its file exists"
    assert resolved.is_file()


@pytest.mark.parametrize(
    "name",
    [EXPECTED_PRETTY_DIR, f"{EXPECTED_PRETTY_DIR}/"],
    ids=["bare", "trailing-slash"],
)
def test_resolve_artifact_rewrites_the_pretty_directory_to_its_overview(
    name: str, populated_artifact_root: Path
) -> None:
    """Naming the PrettyReports directory serves its overview page (AAP 0.3.1).

    *"A request naming the ``cucumber/`` directory serves
    ``cucumber-html-reports/overview-features.html``."*  The trailing slash is
    dropped before validation, so both spellings answer identically.
    """
    assert paths.resolve_artifact(name, base=populated_artifact_root) == (
        paths.pretty_reports_index_path(populated_artifact_root)
    )


def test_resolve_artifact_serves_pages_and_assets_under_the_pretty_tree(
    populated_artifact_root: Path,
) -> None:
    """*"any path under ``cucumber/``"* includes its pages and its assets.

    The PrettyReports pages reference vendored CSS, fonts and scripts relatively
    (AAP 0.3.4), so the route has to serve a whole tree rather than an
    enumerated page list - which is why the allowlist admits the ``cucumber/``
    prefix instead of a fourth file key.
    """
    page = f"{EXPECTED_PRETTY_DIR}/{EXPECTED_PRETTY_SUBDIR}/{PRETTY_PAGE_NAME}"
    asset = (
        f"{EXPECTED_PRETTY_DIR}/{EXPECTED_PRETTY_SUBDIR}/"
        f"{'/'.join(PRETTY_ASSET_COMPONENTS)}"
    )

    assert paths.resolve_artifact(page, base=populated_artifact_root) == (
        paths.pretty_reports_html_dir(populated_artifact_root) / PRETTY_PAGE_NAME
    )
    assert paths.resolve_artifact(asset, base=populated_artifact_root) == (
        paths.pretty_reports_html_dir(populated_artifact_root).joinpath(
            *PRETTY_ASSET_COMPONENTS
        )
    )


def test_resolve_artifact_refuses_an_allowlisted_name_that_does_not_exist(
    tmp_artifact_root: Path,
) -> None:
    """Absence is a rejection, not an empty response (AAP 0.3.1).

    *"Everything else is 404 - ... any absent file."*  The allowlist decides
    which names *may* be served; existence decides whether one *is*.  Asserted
    against an empty checkout root, which is the state before any run.
    """
    for name in EXPECTED_ARTIFACT_KEYS:
        assert paths.resolve_artifact(name, base=tmp_artifact_root) is None


@pytest.mark.parametrize(
    "name",
    [
        "",
        "/etc/passwd",
        f"/{EXPECTED_TARGET_DIR}/{EXPECTED_JSON_NAME}",
        f"//host/share/{EXPECTED_JSON_NAME}",
        "c:/windows/win.ini",
        "C:\\windows\\win.ini",
        f"{EXPECTED_PRETTY_DIR}//{EXPECTED_PRETTY_SUBDIR}/{PRETTY_PAGE_NAME}",
        f"../{EXPECTED_JSON_NAME}",
        f"..\\{EXPECTED_JSON_NAME}",
        f"../../{EXPECTED_JSON_NAME}",
        f"{EXPECTED_PRETTY_DIR}/../{EXPECTED_JSON_NAME}",
        f"{EXPECTED_PRETTY_DIR}/..\\..\\{EXPECTED_JSON_NAME}",
        f"./{EXPECTED_JSON_NAME}",
        ".",
        "..",
        f"{EXPECTED_PRETTY_DIR}/.{EXPECTED_PRETTY_SUBDIR}/{PRETTY_PAGE_NAME}",
        f"{EXPECTED_JSON_NAME}/",
        f"{EXPECTED_PRETTY_DIR}/{EXPECTED_PRETTY_SUBDIR}/",
        f"{EXPECTED_PRETTY_DIR}/{EXPECTED_PRETTY_SUBDIR}",
        "notes.txt",
        f"{EXPECTED_TARGET_DIR}/{EXPECTED_JSON_NAME}",
        f"{EXPECTED_TARGET_DIR}/{EXPECTED_PRETTY_DIR}",
        f"{EXPECTED_FEATURES_DIR}/Crm.feature",
        f"{EXPECTED_JSON_NAME}?download=1",
        f"{EXPECTED_PRETTY_DIR}/{EXPECTED_PRETTY_SUBDIR}/missing.html",
        f"{EXPECTED_PRETTY_DIR}/\x00.html",
        f"{EXPECTED_JSON_NAME}\x00.html",
    ],
    ids=[
        "empty-name",
        "absolute-posix-path",
        "absolute-target-path",
        "unc-prefix",
        "drive-letter",
        "drive-letter-backslash",
        "doubled-separator",
        "parent-traversal",
        "parent-traversal-backslash",
        "double-parent-traversal",
        "nested-parent-traversal",
        "mixed-separator-traversal",
        "current-directory-prefix",
        "bare-dot",
        "bare-dot-dot",
        "dot-prefixed-component",
        "trailing-slash-on-a-file",
        "directory-shaped-request",
        "existing-directory",
        "name-not-on-the-allowlist",
        "target-prefixed-name",
        "target-prefixed-directory",
        "a-feature-file",
        "query-string-in-the-name",
        "absent-page-under-the-pretty-tree",
        "embedded-null-byte",
        "null-byte-after-an-allowlisted-name",
    ],
)
def test_resolve_artifact_rejects_hostile_and_unlisted_names(
    name: str, populated_artifact_root: Path
) -> None:
    """Every rejection returns ``None`` and none of them raises (docstring).

    *"Every rejection returns ``None`` and no rejection raises, which is what
    lets ``app/errors.py`` turn all of them into one plain 404 that leaks no
    filesystem path and is never a 403."*  The matrix runs against a fully
    populated artifact tree, so each name is refused for what it is rather than
    for what happens to be missing - the traversal cases genuinely point at
    files that exist.
    """
    assert paths.resolve_artifact(name, base=populated_artifact_root) is None


@pytest.mark.parametrize(
    "name",
    [
        EXPECTED_WORKERS_DIR,
        f"{EXPECTED_WORKERS_DIR}/",
        f"{EXPECTED_WORKERS_DIR}/worker-4242-0000.json",
        f"{EXPECTED_WORKERS_DIR}\\worker-4242-0000.json",
        f"{EXPECTED_PRETTY_DIR}/../{EXPECTED_WORKERS_DIR}/worker-4242-0000.json",
        f"{EXPECTED_TARGET_DIR}/{EXPECTED_WORKERS_DIR}/worker-4242-0000.json",
    ],
    ids=[
        "bare-directory",
        "directory-with-slash",
        "an-existing-result-file",
        "backslash-separated",
        "reached-through-traversal",
        "target-prefixed",
    ],
)
def test_the_worker_directory_is_never_reachable(
    name: str, populated_artifact_root: Path
) -> None:
    """``.workers/`` is unreachable through the route (AAP 0.3.1, 0.4.1).

    The intermediate JSON is not a published artifact - ``app/cli.py`` deletes
    it before the command returns so the Jenkins publisher never sees it - and
    the AAP singles it out: *"``.workers/`` in particular, which must never be
    reachable"*.  The file named here exists, so a pass means the name was
    refused rather than merely missed.
    """
    assert paths.worker_result_path(0, pid=4242, base=populated_artifact_root).is_file()
    assert paths.resolve_artifact(name, base=populated_artifact_root) is None


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be followed",
)
@pytest.mark.parametrize(
    "through", ["directory", "file"], ids=["linked-dir", "linked-file"]
)
def test_resolve_artifact_refuses_a_symlink_escape(
    through: str, populated_artifact_root: Path, tmp_path: Path
) -> None:
    """A link out of ``target/`` does not widen the allowlist (docstring).

    *"``resolve()`` collapses any remaining relative segments and follows
    symlinks, so a single containment check defeats both traversal and symlink
    escape."*  The containment test is made *after* resolution, which is the
    only ordering that catches this: the name is allowlisted, the component
    names are innocent, and the file behind the link is outside the artifact
    root.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    secret = _write(outside / "secret.html", "<html>secret</html>")

    link_parent = paths.pretty_reports_html_dir(populated_artifact_root)
    if through == "directory":
        link = link_parent / "linked"
        requested = f"{EXPECTED_PRETTY_DIR}/{EXPECTED_PRETTY_SUBDIR}/linked/secret.html"
        destination, is_dir = outside, True
    else:
        link = link_parent / "linked.html"
        requested = f"{EXPECTED_PRETTY_DIR}/{EXPECTED_PRETTY_SUBDIR}/linked.html"
        destination, is_dir = secret, False

    try:
        link.symlink_to(destination, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform policy
        pytest.skip(f"this platform refused to create a symbolic link: {exc}")

    assert link.exists(), "the link was created but does not resolve"
    assert paths.resolve_artifact(requested, base=populated_artifact_root) is None


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be served",
)
def test_resolve_artifact_refuses_a_link_that_stays_inside_the_artifact_root(
    populated_artifact_root: Path,
) -> None:
    """Containment is not enough: a link from ``target`` inward is refused.

    A link whose target is another file *in* the PrettyReports tree passes
    every containment check there is - it resolves inside the artifact root, it
    names an existing regular file, and no component escapes - so it is the case
    that separates "did not escape" from "is what the writer wrote": *"A link
    anywhere from ``target/`` inward is refused outright, not merely required to
    land inside the root: the route must serve the artifacts the writers wrote,
    and a link in the middle of the tree is not one of them."*  The rejection is
    still ``None`` and still raises nothing, so the route answers one plain 404.
    """
    pages = paths.pretty_reports_html_dir(populated_artifact_root)
    link = pages / "linked.html"
    link.symlink_to(pages / PRETTY_PAGE_NAME)
    requested = f"{EXPECTED_PRETTY_DIR}/{EXPECTED_PRETTY_SUBDIR}/{link.name}"

    assert link.is_file(), "the link was created but does not resolve to a file"
    assert _within(link, paths.target_root(populated_artifact_root))
    assert paths.resolve_artifact(requested, base=populated_artifact_root) is None


def test_resolve_artifact_writes_nothing_whatever_it_is_asked(
    populated_artifact_root: Path,
) -> None:
    """Resolution is read-only (docstring: *"Nothing is created and nothing is
    written."*).

    The route is synchronous and read-only per AAP 0.3.1 - *"none writes to disk
    and none starts a run"* - so a full pass over hostile and legitimate names
    must leave the tree byte-identical, directory creation included.
    """
    before = _tree_entries(populated_artifact_root)

    for name in (
        *EXPECTED_ARTIFACT_KEYS,
        "",
        f"../{EXPECTED_JSON_NAME}",
        f"{EXPECTED_WORKERS_DIR}/worker-4242-0000.json",
        f"{EXPECTED_PRETTY_DIR}/{EXPECTED_PRETTY_SUBDIR}/missing.html",
        "unlisted/directory/file.html",
    ):
        paths.resolve_artifact(name, base=populated_artifact_root)

    assert _tree_entries(populated_artifact_root) == before


def test_resolve_artifact_follows_the_base_convention(
    populated_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``resolve_artifact`` takes a string base and defaults to the cwd.

    ``app/web/routes.py`` calls it with no ``base`` inside a request, so the
    working-directory default is the production path and is asserted as such.
    """
    assert paths.resolve_artifact(
        EXPECTED_JSON_NAME, base=str(populated_artifact_root)
    ) == paths.cucumber_json_path(populated_artifact_root)

    monkeypatch.chdir(populated_artifact_root)
    assert paths.resolve_artifact(EXPECTED_JSON_NAME) == (
        Path.cwd() / EXPECTED_TARGET_DIR / EXPECTED_JSON_NAME
    )


# ==========================================================================
# Section 9 - Feature-URI normalization, and the conftest delegation
#
# Contract: AAP deviation 1 moved the features from the Java tree's directory
# to features/ while preserving filenames; AAP 0.4.1 makes this the single
# helper and requires that no test hard-code either prefix.  Every writer test
# compares its output through that helper, so a wrong helper is the one defect
# they could all agree on - which is why it is proved here, on its own.
# ==========================================================================


#: One legacy-prefixed URI, and the rerun-manifest form of it.
LEGACY_URI: Final[str] = f"file:{EXPECTED_LEGACY_PREFIX}Crm.feature"
LEGACY_RERUN_LINE: Final[str] = f"{LEGACY_URI}:9:24"

#: The header line the engine's rerun formatter writes above the entries.  It
#: carries no feature path, so the helper must pass it through untouched.
RERUN_HEADER: Final[str] = "# -- RERUN: 1 scenario(s) failed"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (LEGACY_URI, "file:features/Crm.feature"),
        (f"{EXPECTED_LEGACY_PREFIX}Crm.feature", "features/Crm.feature"),
        (LEGACY_RERUN_LINE, "file:features/Crm.feature:9:24"),
        (f"{EXPECTED_LEGACY_PREFIX}Login.feature:12", "features/Login.feature:12"),
        (
            f"file:{EXPECTED_LEGACY_PREFIX}sub/dir/Crm.feature:3",
            "file:features/sub/dir/Crm.feature:3",
        ),
        (EXPECTED_LEGACY_PREFIX, "features/"),
        ("file:features/Crm.feature", "file:features/Crm.feature"),
        ("features/Crm.feature", "features/Crm.feature"),
        ("file:features/Crm.feature:9:24", "file:features/Crm.feature:9:24"),
        ("", ""),
        ("file:", "file:"),
        (
            f"file:wrapped/{EXPECTED_LEGACY_PREFIX}Crm.feature",
            f"file:wrapped/{EXPECTED_LEGACY_PREFIX}Crm.feature",
        ),
        (
            f"at Crm({EXPECTED_LEGACY_PREFIX}Crm.feature:9)",
            f"at Crm({EXPECTED_LEGACY_PREFIX}Crm.feature:9)",
        ),
        (EXPECTED_LEGACY_PREFIX[:-1], EXPECTED_LEGACY_PREFIX[:-1]),
        (
            f"{EXPECTED_LEGACY_PREFIX.upper()}Crm.feature",
            f"{EXPECTED_LEGACY_PREFIX.upper()}Crm.feature",
        ),
        (
            f"classpath:{EXPECTED_LEGACY_PREFIX}Crm.feature",
            f"classpath:{EXPECTED_LEGACY_PREFIX}Crm.feature",
        ),
    ],
    ids=[
        "file-scheme-legacy-uri",
        "bare-legacy-path",
        "rerun-line-suffixes-preserved",
        "single-line-suffix-preserved",
        "nested-feature-directory",
        "the-prefix-alone",
        "already-normalized-with-scheme",
        "already-normalized-bare",
        "already-normalized-rerun-line",
        "empty-string",
        "scheme-only",
        "legacy-segment-not-a-prefix",
        "legacy-segment-inside-a-message",
        "prefix-without-its-trailing-slash",
        "different-case",
        "a-different-scheme",
    ],
)
def test_normalize_feature_uri_rewrites_only_a_leading_legacy_prefix(
    value: str, expected: str
) -> None:
    """The rewrite is a prefix substitution on the path portion (docstring).

    AAP deviation 1 moved the feature directory while preserving filenames, and
    the helper's contract is narrow on purpose: a leading ``file:`` scheme is
    carried through, everything after the path - notably the rerun manifest's
    ``:9:24`` line numbers (AAP 0.6) - is preserved by construction, and
    *"a path that merely contains the legacy segment somewhere other than at the
    start of its path portion is left alone"*.  That last case is load-bearing:
    the golden report's Java failure messages name the feature they came from,
    and rewriting them would suggest they are comparable against the port's
    output when AAP deviation 16 says they are not.
    """
    assert paths.normalize_feature_uri(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        LEGACY_URI,
        LEGACY_RERUN_LINE,
        f"{EXPECTED_LEGACY_PREFIX}Crm.feature",
        "file:features/Crm.feature",
        "",
        f"file:wrapped/{EXPECTED_LEGACY_PREFIX}Crm.feature",
    ],
    ids=[
        "legacy-uri",
        "legacy-rerun-line",
        "bare-legacy-path",
        "already-normalized",
        "empty",
        "not-a-prefix",
    ],
)
def test_normalize_feature_uri_is_idempotent(value: str) -> None:
    """Normalizing twice changes nothing (docstring).

    *"Anything else is returned unchanged, which makes the function idempotent:
    an already-normalised value comes back as it went in."*  The writers
    normalize as they build, the writer tests normalize the golden baselines
    they compare against, and ``app/reporting/rerun_report.py`` documents
    relying on the property - so a helper that rewrote its own output would
    corrupt a path the second time it saw it.
    """
    once = paths.normalize_feature_uri(value)
    assert paths.normalize_feature_uri(once) == once


def test_normalize_feature_uri_is_the_only_rewrite_the_suite_needs() -> None:
    """The normalized prefix is derived, so the two prefixes cannot drift.

    AAP 0.4.1 makes this the single helper, *"so no test hard-codes either
    prefix"*.  The rewrite's output therefore has to be the feature directory
    the rest of the port uses, not an independently spelled string.
    """
    normalized = paths.normalize_feature_uri(LEGACY_URI)
    assert normalized.startswith(
        f"{paths.FILE_URI_SCHEME}{paths.NORMALIZED_FEATURES_PREFIX}"
    )
    assert paths.LEGACY_FEATURES_PREFIX not in normalized
    assert normalized.endswith("Crm.feature")


def test_conftest_uri_helper_delegates_to_the_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``conftest.normalize_feature_uris`` calls the owner rather than copying it.

    Agreement alone would not prove delegation - two identical implementations
    agree - so the owner's function is replaced with a marker and the wrapper's
    output is required to show it.  ``tests/conftest.py`` states the intent:
    *"The two helpers below *delegate* to it and to nothing else, so this suite
    contains no second implementation of the rule."*
    """
    marker = "<delegated>"
    monkeypatch.setattr(paths, "normalize_feature_uri", lambda _value: marker)

    rewritten = suite_conftest.normalize_feature_uris({"uri": LEGACY_URI})

    assert rewritten == {"uri": marker}


def test_conftest_rerun_helper_delegates_to_the_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``conftest.normalize_rerun_manifest`` calls the owner, line by line.

    The same delegation check for the line-oriented manifest: each non-empty
    line is handed to the owner whole - which is what preserves the ``:9:24``
    suffix - rather than parsed here.
    """
    marker = "<delegated>"
    monkeypatch.setattr(paths, "normalize_feature_uri", lambda _value: marker)

    rewritten = suite_conftest.normalize_rerun_manifest(f"{LEGACY_RERUN_LINE}\n")

    assert rewritten == marker


def test_conftest_uri_helper_agrees_with_the_owner_and_leaves_the_input_alone() -> None:
    """The wrapper rewrites ``uri`` and ``path``, and mutates nothing.

    ``tests/conftest.py`` bounds the walk to the two keys that carry a feature
    path - ``uri`` in the Cucumber-JVM contract, ``uri`` and ``path`` in the
    port's internal schema - and promises the input is never mutated, *"because
    the loader fixtures hand out documents that other assertions in the same
    test may still be comparing against"*.
    """
    document = {
        "uri": LEGACY_URI,
        "elements": [{"path": f"{EXPECTED_LEGACY_PREFIX}Crm.feature", "line": 9}],
        "error_message": f"at Crm({EXPECTED_LEGACY_PREFIX}Crm.feature:9)",
    }
    original = {
        "uri": document["uri"],
        "path": document["elements"][0]["path"],
        "error_message": document["error_message"],
    }

    rewritten = suite_conftest.normalize_feature_uris(document)

    assert rewritten["uri"] == paths.normalize_feature_uri(LEGACY_URI)
    assert rewritten["elements"][0]["path"] == paths.normalize_feature_uri(
        original["path"]
    )
    assert rewritten["elements"][0]["line"] == 9
    assert rewritten["error_message"] == original["error_message"]
    assert document["uri"] == original["uri"]
    assert document["elements"][0]["path"] == original["path"]


def test_conftest_rerun_helper_preserves_the_manifest_shape(golden_rerun: str) -> None:
    """The manifest is rewritten byte for byte apart from the prefix.

    The rerun file is machine input - ``FailedTestRunner.java:11`` declared
    ``features = "@target/rerun.txt"`` and ``run-tests --rerun`` reads it back -
    so ``tests/conftest.py`` promises *"Line structure is preserved exactly, the
    trailing newline included"*.  Each line is compared against the owner's
    answer for that line, and the whole against a prefix substitution, so a
    helper that dropped a newline or re-wrapped a line fails here.
    """
    rewritten = suite_conftest.normalize_rerun_manifest(golden_rerun)

    assert golden_rerun.endswith("\n")
    assert rewritten.endswith("\n")
    assert rewritten.count("\n") == golden_rerun.count("\n")
    assert len(rewritten.splitlines()) == len(golden_rerun.splitlines())
    assert rewritten == "".join(
        paths.normalize_feature_uri(line)
        for line in golden_rerun.splitlines(keepends=True)
    )
    assert rewritten == golden_rerun.replace(
        paths.LEGACY_FEATURES_PREFIX, paths.NORMALIZED_FEATURES_PREFIX
    )
    assert paths.LEGACY_FEATURES_PREFIX not in rewritten


@pytest.mark.parametrize(
    ("text", "expected_lines"),
    [
        ("", 0),
        (f"{LEGACY_RERUN_LINE}\n", 1),
        (f"{LEGACY_RERUN_LINE}\n{EXPECTED_LEGACY_PREFIX}Login.feature:4\n", 2),
        (f"{LEGACY_RERUN_LINE}\r\n{EXPECTED_LEGACY_PREFIX}Login.feature:4\n", 2),
        (f"{RERUN_HEADER}\n{LEGACY_RERUN_LINE}\n", 2),
        (f"{LEGACY_RERUN_LINE}", 1),
    ],
    ids=[
        "empty-manifest",
        "one-entry",
        "two-entries",
        "mixed-line-endings",
        "header-comment-preserved",
        "no-trailing-newline",
    ],
)
def test_conftest_rerun_helper_handles_every_manifest_shape(
    text: str, expected_lines: int
) -> None:
    """Line endings, a header comment and a missing final newline all survive.

    The engine's rerun formatter writes a ``# -- RERUN:`` header above the
    entries, so the helper has to pass an unrelated line through untouched, and
    it must not invent or drop a trailing newline - either would change the
    bytes a second runner parses.
    """
    rewritten = suite_conftest.normalize_rerun_manifest(text)

    assert len(rewritten.splitlines()) == expected_lines
    assert rewritten.endswith("\n") == text.endswith("\n")
    assert rewritten.count("\r\n") == text.count("\r\n")
    assert paths.LEGACY_FEATURES_PREFIX not in rewritten
    if text.startswith("#"):
        assert rewritten.startswith(f"{RERUN_HEADER}\n")


def test_normalized_golden_fixtures_carry_the_ported_prefix(
    golden_rerun: str,
    golden_rerun_normalized: str,
    golden_cucumber: Any,
    golden_cucumber_normalized: Any,
) -> None:
    """The normalized fixtures differ from the verbatim ones in one way only.

    AAP 0.4.1 calls the feature-directory prefix *the one expected difference*
    between a golden baseline and a freshly written artifact, and the writer
    tests compare against the normalized fixtures.  Two facts make that safe and
    are asserted here: every ``uri`` is rewritten, and every ``error_message``
    is not - ``tests/conftest.py`` excludes that key deliberately, because a
    Java stack trace names the feature it came from and AAP deviation 16 rules
    those messages out of parity comparison.
    """
    assert golden_rerun_normalized == suite_conftest.normalize_rerun_manifest(
        golden_rerun
    )
    assert paths.LEGACY_FEATURES_PREFIX in golden_rerun
    assert paths.LEGACY_FEATURES_PREFIX not in golden_rerun_normalized

    def collect(document: Any, key: str) -> list[str]:
        found: list[str] = []
        if isinstance(document, dict):
            for name, value in document.items():
                if name == key and isinstance(value, str):
                    found.append(value)
                else:
                    found.extend(collect(value, key))
        elif isinstance(document, list):
            for item in document:
                found.extend(collect(item, key))
        return found

    verbatim_uris = collect(golden_cucumber, "uri")
    normalized_uris = collect(golden_cucumber_normalized, "uri")
    assert verbatim_uris, "the golden report carries no uri to normalize"
    assert any(paths.LEGACY_FEATURES_PREFIX in uri for uri in verbatim_uris)
    assert normalized_uris == [paths.normalize_feature_uri(uri) for uri in verbatim_uris]
    assert all(paths.LEGACY_FEATURES_PREFIX not in uri for uri in normalized_uris)

    verbatim_messages = collect(golden_cucumber, "error_message")
    assert any(paths.LEGACY_FEATURES_PREFIX in message for message in verbatim_messages)
    assert collect(golden_cucumber_normalized, "error_message") == verbatim_messages


# ==========================================================================
# Section 10 - The ownership invariant across every consumer
#
# Contract: AAP 0.4.2 - "Every writer, the artifact route, the clean step and
# the per-worker invocation take their paths from it, and no other Python
# module contains a path literal."  This is the claim the whole module exists
# to protect, and it is the one a reader cannot verify by inspection.
# ==========================================================================


def test_no_module_but_the_owner_spells_an_owned_path_literal(repo_root: Path) -> None:
    """No string constant outside the owner names an owned path - prose too.

    AAP 0.4.2 states it flatly: *"no other Python module contains a path
    literal"*, with no exemption for documentation, and the reason is drift:
    duplicated path documentation is never executed, so nothing contradicts it
    when the owner's value changes, and a reader who trusts the stale prose is
    misled by the module that is supposed to explain the port.  The scan is
    therefore AST-based over *every* string constant of every module under
    ``app/`` and ``features/`` except the owner - module, class and function
    docstrings included - and f-string fragments count, since
    ``f"{root}/target/x"`` carries its literal in a constant exactly as a plain
    string does.

    Two shapes are not literals and are not counted, because counting them
    would push the cleanup the wrong way - towards deleting the symbolic
    references the invariant exists to encourage: a dotted citation of the
    owner's own symbol (``app.utils.paths.workers_dir``, whose spelling
    contains the intermediate directory's name), and an English compound built
    on the per-worker file's ``worker-`` prefix (``worker-local``,
    ``worker-controlled``).  See :func:`_spells_owned_path`; a literal bounded
    by a quote, a space or a separator, and ``worker-`` followed by a digit or
    a placeholder, are all still reported.

    The remedy for a failure is always the same, and never a carve-out here: an
    offending literal is deleted or rephrased to refer to the owner's constant
    or accessor symbolically.  The failure message is that worklist - every
    offender by file, line and the fragment it spells - because the invariant
    covers every module under ``app/`` and ``features/`` and a reader of the
    failure needs to know which ones.
    """
    offenders: list[str] = []
    owner = Path(paths.__file__).resolve()
    scanned = 0

    for source in _python_sources(repo_root):
        if source.resolve() == owner:
            continue
        scanned += 1
        for lineno, value in _string_constants(_parse(source)):
            for fragment in OWNED_PATH_FRAGMENTS:
                if _spells_owned_path(value, fragment):
                    offenders.append(
                        f"{source.relative_to(repo_root)}:{lineno} spells "
                        f"{fragment!r} in {value[:60]!r}"
                    )

    assert scanned > 0, "the scan found no modules to check"
    assert offenders == [], "path literals outside app/utils/paths.py:\n" + "\n".join(
        offenders
    )


@pytest.mark.parametrize("relative", REQUIRED_PATH_CONSUMERS)
def test_each_required_consumer_takes_its_paths_from_the_owner(
    relative: str, repo_root: Path
) -> None:
    """Every writer, the route, the clean step and the worker import the owner.

    The other half of AAP 0.4.2: taking paths from the owner is not merely "not
    spelling a literal", it is importing the accessor.  AAP 0.4.2 requires all
    eight of :data:`REQUIRED_PATH_CONSUMERS` to exist *and* to take their paths
    from the owner, so an absent module is a failure of the inventory and not a
    case to skip - a skip would let the consumer be deleted, or never written,
    without the suite noticing.  A module that is present and resolves a path
    itself fails on the import assertions below.
    """
    source = repo_root / relative

    assert source.is_file(), (
        f"AAP 0.4.2 requires {relative} to exist and to take its paths from "
        f"app/utils/paths.py; no file is present at {source}"
    )

    imported = _owner_imports(_parse(source))

    assert imported, f"{relative} imports nothing from app.utils.paths"
    for module, names in imported:
        for name in names:
            assert not name.startswith("_"), (
                f"{relative} imports the private name {name} from {module}"
            )
            if module == "app.utils.paths":
                assert name in paths.__all__, (
                    f"{relative} imports {name}, which app.utils.paths does not export"
                )
            else:
                assert name in utils_package.__all__ or name in {"paths", "properties"}, (
                    f"{relative} imports {name}, which app.utils does not export"
                )


def test_no_consumer_reaches_a_private_name_of_the_owner(repo_root: Path) -> None:
    """No module reaches into the owner's private surface.

    The owner keeps its allowlist, its spec index and its worker-name template
    private; a consumer reading one of them would be depending on an
    implementation detail the AAP does not fix, and would bypass the validation
    that surrounds it.
    """
    owner = Path(paths.__file__).resolve()
    offenders: list[str] = []

    for source in _python_sources(repo_root):
        if source.resolve() == owner:
            continue
        for module, names in _owner_imports(_parse(source)):
            offenders.extend(
                f"{source.relative_to(repo_root)} imports {name} from {module}"
                for name in names
                if name.startswith("_")
            )

    assert offenders == [], "private owner names are imported:\n" + "\n".join(offenders)


# ==========================================================================
# Section 11 - No-follow artifact I/O
#
# Contract: the owner's module docstring.  "Validating a *pathname* and then
# opening it leaves a window between the two in which the name can come to
# mean a different object (CWE-367), so the four writers and the artifact
# route do not open artifact paths themselves.  They take an already-verified
# object from here."  Every artifact opened - for reading or for writing - is
# also fstat-ed through its own descriptor and required to be "a regular file
# with exactly one link", and a write is opened without O_TRUNC and truncated
# only once those checks have passed.
#
# Each refusal below is driven by building the shape on a real filesystem, in
# a temporary directory, and asserting both the exception and the fact that
# nothing outside the artifact root was touched.
# ==========================================================================


#: A path component longer than the 255 bytes POSIX ``NAME_MAX`` allows and
#: Windows permits, so every platform refuses it with an :exc:`OSError` that is
#: *not* one of the owner's two translated refusals.  It is how the "anything
#: else the platform reports, unchanged" half of the contract is reached
#: without inventing an error.
TOO_LONG_COMPONENT: Final[str] = "n" * 300


def test_open_artifact_write_creates_the_artifact_and_its_parent(
    tmp_artifact_root: Path,
) -> None:
    """The write helper is a drop-in for the plain builtin.

    A writer calls it exactly as it would call :func:`open`, with the same
    three defaults: text mode, UTF-8, and ``newline="\\n"`` so the byte the
    artifacts require is the byte written on every platform.  The parent is
    created on the way, which is what lets a writer run straight after
    ``--clean`` emptied the build output.
    """
    artifact = paths.cucumber_json_path(tmp_artifact_root)

    with paths.open_artifact_write(artifact) as stream:
        assert isinstance(stream, io.TextIOWrapper)
        assert stream.encoding.lower().replace("-", "") == "utf8"
        stream.write("[]\n")

    assert artifact.parent.is_dir()
    assert artifact.read_bytes() == b"[]\n"
    assert artifact.stat().st_nlink == 1


def test_open_artifact_write_returns_a_binary_stream_when_asked(
    tmp_artifact_root: Path,
) -> None:
    """``binary=True`` hands back the buffered binary stream the docstring names.

    The PrettyReports generator copies vendored fonts and images into the
    emitted tree (AAP 0.3.4), so the write side has to offer bytes as well as
    text; ``encoding`` and ``newline`` are ignored on that branch *"as the
    builtin does"*, which is what makes the helper substitutable for it.
    """
    artifact = paths.pretty_reports_html_dir(tmp_artifact_root) / "logo.png"

    with paths.open_artifact_write(artifact, binary=True) as stream:
        assert isinstance(stream, io.BufferedWriter)
        stream.write(b"\x89PNG\r\n")

    assert artifact.read_bytes() == b"\x89PNG\r\n"


def test_open_artifact_write_truncates_the_previous_run_in_place(
    tmp_artifact_root: Path,
) -> None:
    """The second run replaces the first run's bytes in the same file.

    Two halves of one contract.  The artifact is *truncated*, so a shorter
    report does not leave the tail of a longer one behind - and it is truncated
    **in place**, in the file that was already there, because *"this module
    never deletes anything ... so there is no temporary file to replace and no
    ``unlink`` to perform"*.  The inode is asserted unchanged, which is what
    distinguishes truncating in place from the replace-the-destination strategy
    the owner deliberately does not use.
    """
    artifact = paths.rerun_txt_path(tmp_artifact_root)
    with paths.open_artifact_write(artifact) as stream:
        stream.write("a long first run\nwith two lines\n")
    first_inode = artifact.stat().st_ino

    with paths.open_artifact_write(artifact) as stream:
        stream.write("short\n")

    assert artifact.read_text(encoding="utf-8") == "short\n"
    assert artifact.stat().st_ino == first_inode


def test_open_artifact_read_returns_a_buffered_reader_over_the_bytes(
    tmp_artifact_root: Path,
) -> None:
    """The read helper hands back the buffered binary stream it promises.

    *"An open buffered binary stream, owned by the caller."*  Binary, because
    the artifact route serves bytes and the JSON reader decodes for itself; and
    a stream rather than a path, because *"a path is only ever a statement about
    the past"*.
    """
    artifact = paths.cucumber_json_path(tmp_artifact_root)
    with paths.open_artifact_write(artifact) as stream:
        stream.write("[]\n")

    with paths.open_artifact_read(artifact) as handle:
        assert isinstance(handle, io.BufferedReader)
        assert handle.read() == b"[]\n"


def test_read_artifact_text_decodes_and_translates_newlines(
    tmp_artifact_root: Path,
) -> None:
    """``read_artifact_text`` is what :meth:`~pathlib.Path.read_text` was.

    *"Equivalent to it in what it returns: the bytes are decoded through a text
    wrapper with universal newline translation, so a file written with CRLF
    reads back with ``\\n`` exactly as ``read_text`` gives it.  What differs is
    how the file is opened."*  Equivalence is asserted against ``read_text``
    itself over CRLF content, so the two cannot drift.
    """
    artifact = paths.cucumber_reports_html_path(tmp_artifact_root)
    with paths.open_artifact_write(artifact, binary=True) as stream:
        stream.write(b"<html>\r\n<body/>\r\n</html>")

    text = paths.read_artifact_text(artifact)

    assert text == "<html>\n<body/>\n</html>"
    assert text == artifact.read_text(encoding="utf-8")


def test_open_artifact_read_reports_an_absent_artifact_as_absent(
    tmp_artifact_root: Path,
) -> None:
    """Absence is :exc:`FileNotFoundError`, not a refusal and not a creation.

    *"Nothing is created: a missing directory or a missing file is reported, not
    filled in."*  The distinction is load-bearing for the artifact route and for
    ``run-tests --rerun``: an absent artifact means the writer has not run,
    while an :exc:`~app.utils.paths.ArtifactPathError` means something is
    standing where the artifact belongs, and the caller behaves differently.
    """
    paths.ensure_dir(paths.target_root(tmp_artifact_root))
    absent = paths.cucumber_json_path(tmp_artifact_root)

    with pytest.raises(FileNotFoundError) as failure:
        paths.open_artifact_read(absent)
    assert not isinstance(failure.value, paths.ArtifactPathError)
    assert not absent.exists()


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be written through",
)
def test_open_artifact_write_refuses_a_symlinked_destination(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """A destination that is a link is refused before anything is written.

    This is the refusal the write helper exists for: opening a pathname with
    the plain builtin follows a link out of the artifact root and truncates
    whatever it finds.  The helper tests the entry under the verified parent's
    descriptor first, so the refusal happens *"in every case nothing has been
    written or truncated"* - asserted on the link's target, not merely on the
    exception.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    victim = _write(outside / VICTIM_NAME, VICTIM_CONTENT)

    artifact = paths.cucumber_json_path(tmp_artifact_root)
    paths.ensure_dir(artifact.parent)
    artifact.symlink_to(victim)

    with pytest.raises(paths.ArtifactPathError):
        paths.open_artifact_write(artifact)
    assert victim.read_text(encoding="utf-8") == VICTIM_CONTENT


@pytest.mark.skipif(
    not FIFOS_AVAILABLE,
    reason="this platform cannot create a FIFO, so no artifact path can hold one",
)
def test_open_artifact_write_refuses_a_fifo_destination(
    tmp_artifact_root: Path,
) -> None:
    """A FIFO where the artifact belongs is refused instead of blocking.

    *"Opening a **FIFO** blocks until the other end is opened, so an entry
    replaced by one would hang a writer - or the artifact route - for ever,
    before the check that would have refused it could run."*  Every artifact
    open therefore carries ``O_NONBLOCK``, which makes the platform report
    ``ENXIO`` immediately, and the owner translates that into the refusal below.
    A test that hung instead of failing would be the defect this flag prevents.
    """
    artifact = paths.rerun_txt_path(tmp_artifact_root)
    paths.ensure_dir(artifact.parent)
    os.mkfifo(artifact)

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.open_artifact_write(artifact)
    assert EXPECTED_RERUN_NAME in str(failure.value)


def test_open_artifact_write_surfaces_a_failure_that_is_not_a_refusal(
    tmp_artifact_root: Path,
) -> None:
    """An open failure the owner does not translate reaches the caller as itself.

    Only two ``errno`` values mean "not an artifact" - ``ELOOP`` for a symbolic
    link and ``ENXIO`` for a FIFO with no reader - and the contract for
    everything else is *"the original error, unchanged"*, so a writer's
    ``except OSError`` still reports the real cause (AAP 0.4.1) rather than a
    refusal message that would misdescribe it.  A directory standing where the
    artifact belongs is one such failure: the entry is neither a link nor a
    FIFO, so the platform's own error is what surfaces.
    """
    occupied = paths.ensure_dir(paths.pretty_reports_dir(tmp_artifact_root))

    with pytest.raises(OSError) as failure:
        paths.open_artifact_write(occupied)
    assert not isinstance(failure.value, paths.ArtifactPathError)
    assert occupied.is_dir()
    assert _tree_entries(occupied) == ()


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be read through",
)
def test_open_artifact_read_refuses_a_symlinked_entry(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """A link at the end of the path is refused during the open itself.

    The read side opens the final component ``O_NOFOLLOW`` under the verified
    parent's descriptor, and ``O_NOFOLLOW`` reports a symbolic link as
    ``ELOOP``, which the owner translates: *"the artifact entry ... is a
    symbolic link; refusing to read through it"*.  Refusing during the open is
    what makes it immune to a name that changes after a check - there is no
    check to change the name after.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    secret = _write(outside / "secret.html", "<html>secret</html>")

    artifact = paths.cucumber_reports_html_path(tmp_artifact_root)
    paths.ensure_dir(artifact.parent)
    artifact.symlink_to(secret)

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.open_artifact_read(artifact)
    assert EXPECTED_HTML_NAME in str(failure.value)


def test_open_artifact_read_refuses_a_directory(tmp_artifact_root: Path) -> None:
    """A directory is not a regular file, so it is refused (docstring).

    *"It must be a **regular file**.  A directory, a FIFO or a device is
    refused."*  The check is made with :func:`os.fstat` on the descriptor the
    open produced, not on the name, so its answer *"cannot be invalidated by
    anything that happens to the pathname afterwards"* - and it is the reason
    the artifact route cannot be made to serve a directory's bytes.
    """
    directory = paths.ensure_dir(paths.pretty_reports_dir(tmp_artifact_root))

    with pytest.raises(paths.ArtifactPathError):
        paths.open_artifact_read(directory)


@pytest.mark.skipif(
    not FIFOS_AVAILABLE,
    reason="this platform cannot create a FIFO, so no artifact path can hold one",
)
def test_open_artifact_read_refuses_a_fifo(tmp_artifact_root: Path) -> None:
    """A FIFO would hand a reader bytes no run produced, so it is refused.

    The read side's counterpart to the write-side ``ENXIO`` case: opening a
    FIFO for reading with ``O_NONBLOCK`` *succeeds* rather than failing, so the
    only thing that refuses it is the :func:`os.fstat` of the descriptor -
    *"a FIFO in particular would block a writer for ever and hand a reader bytes
    no run ever produced"*.  This is why the mode check is not redundant with
    the flags.
    """
    artifact = paths.cucumber_json_path(tmp_artifact_root)
    paths.ensure_dir(artifact.parent)
    os.mkfifo(artifact)

    with pytest.raises(paths.ArtifactPathError):
        paths.open_artifact_read(artifact)


@pytest.mark.skipif(
    not HARD_LINKS_AVAILABLE,
    reason="this platform cannot create hard links, so no entry can carry two",
)
def test_open_artifact_read_refuses_a_hard_linked_entry(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """An entry that is also a file elsewhere is refused on read (docstring).

    *"``O_NOFOLLOW`` refuses a *symbolic* link, and nothing about it refuses a
    **hard** link: an entry hard-linked to a file outside the artifact root is
    that file, so ... reading the artifact would disclose it, with no symlink
    anywhere in the path."*  The link count comes from the same
    :func:`os.fstat` as the mode, so the read side refuses it even though the
    entry it was asked for is a perfectly ordinary regular file.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    victim = _write(outside / VICTIM_NAME, VICTIM_CONTENT)

    artifact = paths.cucumber_json_path(tmp_artifact_root)
    paths.ensure_dir(artifact.parent)
    os.link(victim, artifact)

    with pytest.raises(paths.ArtifactPathError):
        paths.open_artifact_read(artifact)
    assert artifact.stat().st_nlink == 2


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be traversed",
)
def test_open_artifact_read_refuses_a_symlinked_owned_component(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """A link standing in for ``target/`` is refused on the read side too.

    The read side never creates a directory, so it meets a symlinked component
    at the ``O_NOFOLLOW`` open of that component rather than at a ``mkdir``:
    either way *"``target`` itself and every component below it"* is refused
    rather than traversed, which is what keeps the artifact route from serving
    a tree the operator's own layout happens to link to.  The file behind the
    link exists, so the refusal is for the link and not for absence.

    Which of the walk's two messages names it depends on the ``errno`` the
    platform reports when ``O_NOFOLLOW`` and ``O_DIRECTORY`` meet a link -
    ``ENOTDIR`` here, ``ELOOP`` where ``O_DIRECTORY`` is unavailable, which
    Section 12 pins separately - so what is asserted is the refusal and the
    component it names, not the wording of one platform's answer.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    _write(elsewhere / EXPECTED_JSON_NAME, "[]")
    paths.target_root(tmp_artifact_root).symlink_to(
        elsewhere, target_is_directory=True
    )

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.open_artifact_read(paths.cucumber_json_path(tmp_artifact_root))
    assert EXPECTED_TARGET_DIR in str(failure.value)


def test_open_artifact_read_refuses_a_non_directory_owned_component(
    tmp_artifact_root: Path,
) -> None:
    """A file where a directory belongs is named as the component it is.

    The walk's other refusal: *"the artifact path component ... is not a
    directory"*.  It is asserted separately from the symlink case because the
    two states arise differently - a link is refused for what it points at, a
    regular file for what it is - and because a writer diagnosing a failed run
    needs the offending component named rather than an ``ENOTDIR`` reported
    against the child it was trying to reach.
    """
    _write(paths.target_root(tmp_artifact_root), "not a directory")

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.open_artifact_read(paths.cucumber_json_path(tmp_artifact_root))
    assert EXPECTED_TARGET_DIR in str(failure.value)


def test_open_artifact_read_surfaces_a_component_failure_that_is_not_a_refusal(
    tmp_artifact_root: Path,
) -> None:
    """A component the platform rejects for its own reasons is not translated.

    The walk translates exactly two conditions - not a directory, and a
    symbolic link - and re-raises *"anything else the platform reports - a
    denied traversal, a read-only filesystem, a name too long - unchanged"*.
    A name longer than ``NAME_MAX`` is the one such failure a test can produce
    on demand without privileges, and the assertion is that it arrives as
    itself rather than as a refusal that would misdescribe it.
    """
    paths.ensure_dir(paths.target_root(tmp_artifact_root))
    unreachable = (
        paths.target_root(tmp_artifact_root) / TOO_LONG_COMPONENT / EXPECTED_JSON_NAME
    )

    with pytest.raises(OSError) as failure:
        paths.open_artifact_read(unreachable)
    assert not isinstance(failure.value, paths.ArtifactPathError)


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be read through",
)
def test_read_artifact_text_refuses_whatever_the_read_helper_refuses(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """The text reader inherits every refusal instead of re-deriving one.

    ``read_artifact_text`` documents its failures as *"As
    :func:`open_artifact_read`"*, and it earns that by opening through it: one
    verification path, so a caller that reads text cannot be given a weaker
    guarantee than one that reads bytes.  Asserted with the shape the plain
    :meth:`~pathlib.Path.read_text` it replaces would have followed happily.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    secret = _write(outside / VICTIM_NAME, VICTIM_CONTENT)

    artifact = paths.cucumber_json_path(tmp_artifact_root)
    paths.ensure_dir(artifact.parent)
    artifact.symlink_to(secret)

    with pytest.raises(paths.ArtifactPathError):
        paths.read_artifact_text(artifact)
    assert artifact.read_text(encoding="utf-8") == VICTIM_CONTENT


def test_open_resolved_artifact_opens_the_file_it_validated(
    populated_artifact_root: Path,
) -> None:
    """Validation and the open are one operation, and the handle is the answer.

    *"There is no window between the check and the open for a symlink to be
    swapped into (CWE-367), and the caller never has to name the path again: it
    serves the handle."*  The returned path is asserted to agree with
    :func:`~app.utils.paths.resolve_artifact` - the same allowlist, so the two
    cannot drift - and the handle is asserted to hold that file's bytes.
    """
    resolved_and_handle = paths.open_resolved_artifact(
        EXPECTED_JSON_NAME, base=populated_artifact_root
    )

    assert resolved_and_handle is not None
    resolved, handle = resolved_and_handle
    with handle:
        assert isinstance(handle, io.BufferedReader)
        assert handle.read() == b"[]"
    assert resolved == paths.resolve_artifact(
        EXPECTED_JSON_NAME, base=populated_artifact_root
    )


def test_open_resolved_artifact_serves_the_one_authorized_directory_spelling(
    populated_artifact_root: Path,
) -> None:
    """The ``cucumber`` key opens the overview page, not a directory (AAP 0.3.1).

    *"A request naming the ``cucumber/`` directory serves
    ``cucumber-html-reports/overview-features.html``."*  The rewrite happens
    inside the shared validator, which is why the opening entry point needs no
    directory fallback of its own - and must not have one, since a generic
    fallback *"can only map something else"*.
    """
    resolved_and_handle = paths.open_resolved_artifact(
        EXPECTED_PRETTY_DIR, base=populated_artifact_root
    )

    assert resolved_and_handle is not None
    resolved, handle = resolved_and_handle
    with handle:
        assert handle.read() == b"<html>overview</html>"
    assert resolved == paths.pretty_reports_index_path(populated_artifact_root)


@pytest.mark.parametrize(
    "name",
    [
        "",
        f"../{EXPECTED_JSON_NAME}",
        f"{EXPECTED_WORKERS_DIR}/worker-4242-0000.json",
        "notes.txt",
        f"{EXPECTED_PRETTY_DIR}/{EXPECTED_PRETTY_SUBDIR}/missing.html",
        f"{EXPECTED_PRETTY_DIR}/{EXPECTED_PRETTY_SUBDIR}",
        f"{EXPECTED_PRETTY_DIR}/\x00.html",
    ],
    ids=[
        "empty-name",
        "parent-traversal",
        "a-worker-intermediate",
        "name-not-on-the-allowlist",
        "absent-page-under-the-pretty-tree",
        "an-existing-directory",
        "embedded-null-byte",
    ],
)
def test_open_resolved_artifact_rejects_without_ever_raising(
    name: str, populated_artifact_root: Path
) -> None:
    """Every rejection is ``None``: *"Raises: Nothing.  Ever."*

    The rejection classes are the allowlist's own, plus the two the open
    contributes - an absent file and a directory, which
    :func:`~app.utils.paths.resolve_artifact` rejects with a stat and this
    function rejects by failing to open.  All of them are absorbed *"so that
    ``app/errors.py`` can answer one plain 404 that discloses no filesystem
    path and no attempted name"*, and a raised
    :exc:`~app.utils.paths.ArtifactPathError` would instead reach the route as a
    500.  The tree is snapshotted too, because the route is read-only.
    """
    before = _tree_entries(populated_artifact_root)

    assert paths.open_resolved_artifact(name, base=populated_artifact_root) is None
    assert _tree_entries(populated_artifact_root) == before


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be followed",
)
def test_open_resolved_artifact_refuses_a_link_out_of_the_artifact_root(
    populated_artifact_root: Path, tmp_path: Path
) -> None:
    """An escaping link is rejected before anything is opened.

    The containment check is made on the resolved candidate *"before anything
    is opened, so a lexically escaping name never reaches an open call on a
    platform without ``O_NOFOLLOW``"* - the ordering that makes the fallback
    branch safe as well as the primary one.  The file behind the link exists and
    is readable, so a pass means the escape was refused rather than missed.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    secret = _write(outside / "secret.html", "<html>secret</html>")

    link = paths.pretty_reports_html_dir(populated_artifact_root) / "linked.html"
    link.symlink_to(secret)
    requested = f"{EXPECTED_PRETTY_DIR}/{EXPECTED_PRETTY_SUBDIR}/{link.name}"

    assert link.is_file(), "the link was created but does not resolve to a file"
    assert paths.open_resolved_artifact(requested, base=populated_artifact_root) is None


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be served",
)
def test_open_resolved_artifact_refuses_a_link_that_stays_inside_the_root(
    populated_artifact_root: Path,
) -> None:
    """A contained link is refused by the open, and still answers ``None``.

    Containment cannot decide this one - the link's target is another page of
    the PrettyReports tree - so the refusal comes from the open itself, and the
    entry point absorbs it: *"``ArtifactPathError`` for a symlink or a
    non-regular file, ``FileNotFoundError`` for an absent one,
    ``PermissionError`` for an unreadable one: all one 404."*  This is the case
    that proves the two exits from this function agree with
    :func:`~app.utils.paths.resolve_artifact` about one request.
    """
    pages = paths.pretty_reports_html_dir(populated_artifact_root)
    link = pages / "linked.html"
    link.symlink_to(pages / PRETTY_PAGE_NAME)
    requested = f"{EXPECTED_PRETTY_DIR}/{EXPECTED_PRETTY_SUBDIR}/{link.name}"

    assert link.is_file(), "the link was created but does not resolve to a file"
    assert paths.open_resolved_artifact(requested, base=populated_artifact_root) is None
    assert paths.resolve_artifact(requested, base=populated_artifact_root) is None


# ==========================================================================
# Section 12 - The portability fallback, and the seams it needs
#
# Contract: the owner's module docstring.  "``O_NOFOLLOW`` and the ``dir_fd``
# parameter are POSIX-only, while AAP 0.8 requires Windows, Linux and macOS.
# Availability is therefore detected rather than assumed.  Where the primitives
# are missing the helpers refuse a symlinked component found by is_symlink(),
# open the path ordinarily, and then confirm that the object they hold open is
# the one the name refers to, by comparing device and inode with a no-follow
# os.lstat of the same name."
#
# This host has the primitives, so the fallback is unreachable by any
# filesystem shape.  It is reached the only honest way left: by clearing the
# very module attributes the owner derives from ``getattr(os, "O_NOFOLLOW",
# 0)`` and reads on every call, which reproduces a platform that lacks them
# rather than faking a result.  The two race cases go further and make the
# name change *during* the open, through a wrapper around the ``os.open`` the
# owner calls - a real rename and a real unlink on a real filesystem, applied
# at the one instant a test cannot otherwise hit.  No private helper is called
# directly anywhere in this section.
# ==========================================================================


def _seam_that_changes_the_name(
    destination: Path, change: Callable[[], None]
) -> Callable[..., int]:
    """Build an :func:`os.open` replacement that alters the name after opening.

    The check-to-open window is a window in time, so a test that waits for it
    to be exploited is a race rather than a test.  This closes it
    deterministically: the real :func:`os.open` runs, the descriptor it produced
    is kept, and ``change`` then makes the *name* refer to something else -
    which is exactly the state
    ``app.utils.paths._verify_opened_by_name`` exists to detect.

    Every call for any other path is delegated unchanged, so nothing else in
    the process is affected while the replacement is installed.

    :param destination: The one path the change is applied to.
    :param change: What to do to that name once the descriptor exists.
    :returns: A drop-in replacement for :func:`os.open`.
    """
    real_open = os.open

    def opening(path: Any, flags: int, *rest: Any, **options: Any) -> int:
        handle = real_open(path, flags, *rest, **options)
        named = os.fspath(path) if isinstance(path, (str, os.PathLike)) else None
        if named == str(destination):
            change()
        return handle

    return opening


@pytest.fixture
def without_no_follow_support(monkeypatch: pytest.MonkeyPatch) -> None:
    """Present the owner with a platform that has neither primitive.

    Both attributes are the owner's own portability seam - ``_O_NOFOLLOW`` is
    ``getattr(os, "O_NOFOLLOW", 0)`` and ``_NO_FOLLOW_SUPPORTED`` is the
    conjunction that also requires ``dir_fd`` support - and the owner reads them
    on every call, so clearing them here selects the fallback branch for the
    duration of one test exactly as importing the module on Windows would.  The
    flag is cleared as well as the predicate: leaving ``O_NOFOLLOW`` in the open
    flags would keep the kernel refusing links that the emulated platform has to
    refuse for itself, and the point of the branch is what it does *without* the
    kernel's help.

    :param monkeypatch: pytest's attribute patcher, which restores both values.
    """
    monkeypatch.setattr(paths, "_NO_FOLLOW_SUPPORTED", False)
    monkeypatch.setattr(paths, "_O_NOFOLLOW", 0)
    # The publication predicate is the same seam one level up: it additionally
    # requires descriptor-relative rename and removal, so a platform without
    # O_NOFOLLOW has neither and the emulation has to clear both or the
    # publication helpers would take a branch the emulated platform cannot.
    monkeypatch.setattr(paths, "_PUBLICATION_SUPPORTED", False)


def test_fallback_round_trips_an_artifact_through_the_lexical_walk(
    tmp_artifact_root: Path, without_no_follow_support: None
) -> None:
    """Without the primitives the helpers still create, write and read.

    The first thing the fallback has to be is *correct*: AAP 0.8 requires the
    port to run on Windows, so a writer must be able to create its parent, write
    its artifact and read it back with no ``O_NOFOLLOW`` and no ``dir_fd``
    anywhere.  The verification it does instead - the lexical walk, then the
    device-and-inode comparison against the name - has to be invisible to a
    legitimate write, which is what this asserts.
    """
    artifact = paths.cucumber_json_path(tmp_artifact_root)

    with paths.open_artifact_write(artifact) as stream:
        stream.write("[]\n")

    assert artifact.parent.is_dir()
    assert paths.read_artifact_text(artifact) == "[]\n"
    with paths.open_artifact_read(artifact) as handle:
        assert isinstance(handle, io.BufferedReader)
        assert handle.read() == b"[]\n"


def test_fallback_ensure_dir_and_ensure_parent_create_the_tree(
    tmp_artifact_root: Path, without_no_follow_support: None
) -> None:
    """Both directory helpers create ordinarily once the walk has passed.

    On the fallback branch the owned components are checked with
    :meth:`~pathlib.Path.is_symlink` and the directory is then created the
    ordinary way, so the observable result is identical to the primary branch:
    the PrettyReports tree comes into being three levels down, and
    ``ensure_parent`` prepares a parent without bringing the artifact itself
    into existence.
    """
    pages = paths.ensure_dir(paths.pretty_reports_html_dir(tmp_artifact_root))
    artifact = paths.ensure_parent(paths.rerun_txt_path(tmp_artifact_root))

    assert pages.is_dir()
    assert artifact.parent.is_dir()
    assert not artifact.exists()


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be refused",
)
@pytest.mark.parametrize(
    "helper",
    [paths.ensure_dir, paths.ensure_parent, paths.open_artifact_write],
    ids=["ensure_dir", "ensure_parent", "open_artifact_write"],
)
def test_fallback_refuses_a_symlinked_component_lexically(
    helper: Callable[..., Any],
    tmp_artifact_root: Path,
    tmp_path: Path,
    without_no_follow_support: None,
) -> None:
    """Every write-side entry point refuses a linked component on the fallback.

    *"Where the primitives are missing the helpers refuse a symlinked component
    found by :meth:`~pathlib.Path.is_symlink`."*  The walk is shared, so all
    three entry points are asserted against one shape - a link standing in for
    the build-output directory - and the link's target is asserted empty
    afterwards: a refusal that created the tree inside the link's destination
    first would be no refusal at all.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    paths.target_root(tmp_artifact_root).symlink_to(outside, target_is_directory=True)

    with pytest.raises(paths.ArtifactPathError) as failure:
        helper(paths.cucumber_json_path(tmp_artifact_root))
    assert EXPECTED_TARGET_DIR in str(failure.value)
    assert _tree_entries(outside) == ()


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be refused",
)
def test_fallback_refuses_a_resolved_artifact_reached_through_a_link(
    populated_artifact_root: Path, without_no_follow_support: None
) -> None:
    """The route's validator applies the same rule on the fallback branch.

    ``resolve_artifact`` returns a path rather than a handle, so it verifies
    with the *"validation-only counterpart"* of the read helper - which has the
    same two branches and therefore has to refuse the same request on both.
    Asserted with a link inside the artifact root, the case containment cannot
    decide, and with a legitimate artifact in the same tree so the branch is
    shown to be discriminating rather than simply refusing.
    """
    pages = paths.pretty_reports_html_dir(populated_artifact_root)
    link = pages / "linked.html"
    link.symlink_to(pages / PRETTY_PAGE_NAME)

    refused = f"{EXPECTED_PRETTY_DIR}/{EXPECTED_PRETTY_SUBDIR}/{link.name}"
    assert paths.resolve_artifact(refused, base=populated_artifact_root) is None
    assert paths.resolve_artifact(
        EXPECTED_JSON_NAME, base=populated_artifact_root
    ) == paths.cucumber_json_path(populated_artifact_root)


def test_fallback_refuses_a_destination_swapped_while_it_was_being_opened(
    tmp_artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    without_no_follow_support: None,
) -> None:
    """The fallback is not a check-then-open race, and this is why.

    *"Something swapped in between the lexical check and the open fails that
    comparison and is refused - before any truncation, since the write path
    truncates last."*  The swap is performed at the only moment that matters,
    through the wrapper :func:`_seam_that_changes_the_name` installs around the
    :func:`os.open` the owner calls: the descriptor holds the file the walk
    approved, while the name now refers to a different inode.  What the
    assertion shows is that the swapped-in file keeps every byte it had - the
    refusal lands before the ``ftruncate`` and before anything is written, so
    the attacker's file is neither emptied nor filled with a report.
    """
    artifact = paths.ensure_parent(paths.cucumber_json_path(tmp_artifact_root))
    artifact.write_text("[]", encoding="utf-8")
    decoy = _write(artifact.parent / "decoy.json", VICTIM_CONTENT)

    monkeypatch.setattr(
        os,
        "open",
        _seam_that_changes_the_name(artifact, lambda: os.replace(decoy, artifact)),
    )

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.open_artifact_write(artifact)
    assert EXPECTED_JSON_NAME in str(failure.value)
    assert artifact.read_text(encoding="utf-8") == VICTIM_CONTENT


def test_fallback_refuses_a_destination_that_disappeared_while_being_opened(
    tmp_artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    without_no_follow_support: None,
) -> None:
    """A name that no longer exists is refused rather than written blind.

    The second outcome of the same comparison: the descriptor is live but the
    name is gone, so the owner *"cannot establish that it landed on the wrong
    object"* and refuses instead of writing to an unnamed inode whose bytes no
    reader could ever find.  Driven through the same ``os.open`` wrapper, with a
    real unlink, because a file cannot be made to vanish at that instant any
    other way.
    """
    artifact = paths.ensure_parent(paths.rerun_txt_path(tmp_artifact_root))
    artifact.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        os,
        "open",
        _seam_that_changes_the_name(artifact, lambda: os.unlink(artifact)),
    )

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.open_artifact_write(artifact)
    assert EXPECTED_RERUN_NAME in str(failure.value)
    assert not artifact.exists()


def test_fallback_surfaces_the_failures_it_does_not_translate(
    tmp_artifact_root: Path, without_no_follow_support: None
) -> None:
    """The fallback changes how a path is verified, not which failures surface.

    Both branches translate exactly ``ELOOP`` and ``ENXIO`` and re-raise
    everything else, so a caller's error handling cannot depend on which one ran:
    an absent artifact is still :exc:`FileNotFoundError` - the distinction
    ``run-tests --rerun`` and the artifact route both rely on - and a directory
    standing where an artifact belongs is still the platform's own error rather
    than a refusal message that would misdescribe it.
    """
    paths.ensure_dir(paths.target_root(tmp_artifact_root))
    absent = paths.cucumber_json_path(tmp_artifact_root)
    occupied = paths.ensure_dir(paths.pretty_reports_dir(tmp_artifact_root))

    with pytest.raises(FileNotFoundError) as absence:
        paths.open_artifact_read(absent)
    assert not isinstance(absence.value, paths.ArtifactPathError)

    with pytest.raises(OSError) as failure:
        paths.open_artifact_write(occupied)
    assert not isinstance(failure.value, paths.ArtifactPathError)


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be traversed",
)
def test_a_linked_component_is_refused_as_a_link_without_o_directory(
    tmp_artifact_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk names a linked component as a link on either kind of platform.

    ``O_NOFOLLOW`` reports a symbolic link as ``ELOOP``, and the walk translates
    that into *"is a symbolic link; refusing to traverse it"*.  Reaching it
    needs the owner's other portability seam: where ``O_DIRECTORY`` is available
    - it is here - the kernel answers ``ENOTDIR`` for the same shape and the
    walk's first handler names it as "not a directory", so the ``ELOOP`` handler
    is unreachable by any filesystem shape on this host.  Clearing
    ``_O_DIRECTORY``, which the owner derives from ``getattr(os, "O_DIRECTORY",
    0)`` and passes to every component open, reproduces the platform that does
    answer ``ELOOP``; the shape itself is a real link and the refusal is the
    owner's own.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    _write(elsewhere / EXPECTED_JSON_NAME, "[]")
    paths.target_root(tmp_artifact_root).symlink_to(
        elsewhere, target_is_directory=True
    )
    monkeypatch.setattr(paths, "_O_DIRECTORY", 0)

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.open_artifact_read(paths.cucumber_json_path(tmp_artifact_root))
    assert EXPECTED_TARGET_DIR in str(failure.value)
    # "link" appears in this handler's message and not in the not-a-directory
    # one, so it is what distinguishes the two answers for the same shape.
    assert "link" in str(failure.value)
    assert failure.value.errno is None


# ==========================================================================
# Section 13 - The creation-mode policy
#
# Contract: the permissions section of the owner's module docstring.  The
# artifacts carry the run's evidence - failure screenshots, step arguments
# substituted from the Examples tables, the configured URLs - so a generated
# file is owner-only and a generated directory is owner-only, and a mode
# argument alone cannot deliver that: it applies only to an object the call
# creates, so an artifact a previous run left at 0644 keeps those bits unless
# something tightens it through its own descriptor (CWE-732/CWE-359).
# ==========================================================================


@pytest.fixture(params=[True, False], ids=["primitives", "fallback"])
def either_branch(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    """Run one assertion against the descriptor-bound branch and the fallback.

    The two branches implement one contract by different means - a held
    directory descriptor with ``O_NOFOLLOW``, or the bound identity chain of
    :class:`app.utils.paths._FallbackAnchor` - and a property that holds on
    only one of them is a property Windows or POSIX does not get.  Cleared
    through the owner's own portability seams, exactly as
    :fixture:`without_no_follow_support` does it.

    :param request: pytest's parametrization handle.
    :param monkeypatch: pytest's attribute patcher, which restores the flags.
    :returns: ``True`` for the primitive branch, ``False`` for the fallback.
    """
    if not request.param:
        monkeypatch.setattr(paths, "_NO_FOLLOW_SUPPORTED", False)
        monkeypatch.setattr(paths, "_O_NOFOLLOW", 0)
        monkeypatch.setattr(paths, "_PUBLICATION_SUPPORTED", False)
    return bool(request.param)


def _mode(path: Path) -> int:
    """The permission bits of ``path``, without the file type.

    :param path: Entry to inspect.
    :returns: ``st_mode`` masked to the twelve permission and special bits.
    """
    return path.stat().st_mode & 0o7777


def _assert_owner_only(path: Path) -> None:
    """Assert that nothing but the owner can reach ``path``.

    The group and other bits are asserted absent rather than the whole mode
    asserted equal to ``0o700``: a set-group-id build directory keeps that bit
    - it grants the group nothing once the access bits are gone - and the
    policy is about access, not about the exact integer.

    :param path: Entry to check.
    """
    mode = _mode(path)
    assert mode & paths.ARTIFACT_MODE_MASK == 0, (
        f"{path} is reachable by the group or by others: {oct(mode)}"
    )
    assert mode & 0o700, f"{path} is not reachable by its owner: {oct(mode)}"


def test_the_mode_policy_constants_are_owner_only_values() -> None:
    """The three policy constants say owner-only, and agree with each other.

    They are part of the module's published surface because the writers' own
    tests assert the modes of what they produced, and a test that read the
    value out of the mode it is checking would pass against any policy.
    """
    assert paths.ARTIFACT_DIR_MODE == 0o700
    assert paths.ARTIFACT_FILE_MODE == 0o600
    assert paths.ARTIFACT_MODE_MASK == 0o077
    assert paths.ARTIFACT_DIR_MODE & paths.ARTIFACT_MODE_MASK == 0
    assert paths.ARTIFACT_FILE_MODE & paths.ARTIFACT_MODE_MASK == 0


@pytest.mark.skipif(
    not hasattr(os, "fchmod"),
    reason="this platform expresses permissions as ACLs, so the policy does not apply",
)
def test_open_artifact_write_creates_an_owner_only_artifact_and_tree(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """A written artifact, and every owned directory above it, is owner-only.

    The whole chain is asserted, not just the file: ``target/`` and
    ``target/.workers/`` are created by the same helpers, and a world-readable
    directory discloses the names of the shards and the artifacts inside it
    even where the files themselves are tight.
    """
    artifact = paths.worker_result_path(0, pid=4242, base=tmp_artifact_root)

    with paths.open_artifact_write(artifact) as stream:
        stream.write("{}\n")

    _assert_owner_only(artifact)
    assert _mode(artifact) == paths.ARTIFACT_FILE_MODE
    _assert_owner_only(paths.target_root(tmp_artifact_root))
    _assert_owner_only(paths.workers_dir(tmp_artifact_root))


@pytest.mark.skipif(
    not hasattr(os, "fchmod"),
    reason="this platform expresses permissions as ACLs, so the policy does not apply",
)
def test_a_previously_permissive_artifact_is_tightened_before_it_is_rewritten(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """A ``0644`` artifact from an earlier run does not keep those bits.

    This is the case a creation mode cannot cover, and the one the review
    measured: ``run-tests --no-clean`` rewrites the artifact an earlier run
    created, so the mode argument does not apply and the file keeps whatever
    it had.  The tightening is made through the descriptor the write holds and
    *before* the truncation, so the bits are gone before the new content
    exists.
    """
    artifact = paths.cucumber_json_path(tmp_artifact_root)
    with paths.open_artifact_write(artifact) as stream:
        stream.write("[]\n")
    os.chmod(artifact, 0o644)
    os.chmod(paths.target_root(tmp_artifact_root), 0o755)

    with paths.open_artifact_write(artifact) as stream:
        stream.write("[1]\n")

    assert artifact.read_text(encoding="utf-8") == "[1]\n"
    _assert_owner_only(artifact)
    _assert_owner_only(paths.target_root(tmp_artifact_root))


@pytest.mark.skipif(
    not hasattr(os, "fchmod"),
    reason="this platform expresses permissions as ACLs, so the policy does not apply",
)
def test_reading_an_artifact_leaves_the_modes_it_found(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """The read side applies no policy: reading must not alter the workspace.

    Deliberately asymmetric.  Tightening on a read would make
    ``GET /artifacts/<name>`` a writer of directory metadata - and the process
    serving a report may not own the tree it serves - so the policy is applied
    where the content is produced and nowhere else.
    """
    artifact = _write(paths.cucumber_json_path(tmp_artifact_root), "[]")
    os.chmod(artifact, 0o644)
    os.chmod(paths.target_root(tmp_artifact_root), 0o755)

    assert paths.read_artifact_text(artifact) == "[]"

    assert _mode(artifact) == 0o644
    assert _mode(paths.target_root(tmp_artifact_root)) == 0o755


@pytest.mark.skipif(
    not hasattr(os, "fchmod"),
    reason="this platform expresses permissions as ACLs, so the policy does not apply",
)
def test_a_write_is_refused_when_the_artifact_cannot_be_tightened(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An artifact that stays group-readable fails the write rather than ships.

    A filesystem that ignores :func:`os.chmod` - some network and FAT mounts -
    cannot restrict the file, and publishing the run's evidence
    world-readable is the outcome the policy exists to prevent, so the writer
    failure is the honest answer.  Driven through :func:`os.fchmod`, which is
    the call the owner makes, rather than by finding such a filesystem.
    """
    artifact = _write(paths.cucumber_json_path(tmp_artifact_root), "[]")
    os.chmod(artifact, 0o646)

    def refusing(*_args: object, **_options: object) -> None:
        raise PermissionError(errno.EPERM, "chmod is not supported here")

    monkeypatch.setattr(os, "fchmod", refusing)

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.open_artifact_write(artifact)
    assert "owner" in str(failure.value)
    # The refusal happens before the truncation, so the previous artifact is
    # still the complete one.
    assert artifact.read_text(encoding="utf-8") == "[]"


# ==========================================================================
# Section 14 - Atomic file publication
#
# Contract: :func:`app.utils.paths.publish_artifact_file`.  The single-page
# HTML artifact may be open in a browser or being archived while the next run
# rewrites it, so it is published by rename from a temporary in its own
# verified directory - and the whole sequence, creation to rename, runs under
# the descriptor that was verified, because a rename resolved from a pathname
# can land in a directory that has since become a link (CWE-59/CWE-367).
# ==========================================================================


def test_publish_artifact_file_writes_the_bytes_and_leaves_no_temporary(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """The published artifact holds exactly what was written, and stands alone.

    The temporary is dot-prefixed while it exists - so
    :func:`~app.utils.paths.resolve_artifact` cannot serve it - and it is gone
    afterwards, because the rename consumed it.  Nothing else is created beside
    the artifact, which is the single-file contract of this artifact.
    """
    artifact = paths.cucumber_reports_html_path(tmp_artifact_root)

    with paths.publish_artifact_file(artifact) as stream:
        stream.write("<!DOCTYPE html>\n<title>Cucumber</title>\n")

    assert artifact.read_text(encoding="utf-8") == (
        "<!DOCTYPE html>\n<title>Cucumber</title>\n"
    )
    assert _tree_entries(paths.target_root(tmp_artifact_root)) == (
        EXPECTED_HTML_NAME,
    )


@pytest.mark.skipif(
    not hasattr(os, "fchmod"),
    reason="this platform expresses permissions as ACLs, so the policy does not apply",
)
def test_a_published_artifact_is_owner_only(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """The publication route applies the same mode policy as a plain write.

    The temporary is created ``O_CREAT|O_EXCL`` with
    :data:`~app.utils.paths.ARTIFACT_FILE_MODE`, and the rename carries that
    mode onto the destination, so a run cannot loosen an artifact's permissions
    by publishing it rather than writing it in place.
    """
    artifact = paths.cucumber_reports_html_path(tmp_artifact_root)

    with paths.publish_artifact_file(artifact) as stream:
        stream.write("<html></html>\n")

    assert _mode(artifact) == paths.ARTIFACT_FILE_MODE


def test_publish_artifact_file_keeps_the_previous_artifact_on_a_failure(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """A fault mid-write leaves the last complete artifact and no scratch.

    This is the difference from opening the destination: ``"w"`` truncates
    before the first byte exists, so a failure after that destroys an artifact
    that was complete.  Here the destination is only ever reached by the
    rename, which never happens, and the temporary is removed on the way out.
    """
    artifact = paths.cucumber_reports_html_path(tmp_artifact_root)
    with paths.publish_artifact_file(artifact) as stream:
        stream.write("<html>first</html>\n")

    with pytest.raises(RuntimeError, match="render failed"):
        with paths.publish_artifact_file(artifact) as stream:
            stream.write("<html>partial")
            raise RuntimeError("render failed")

    assert artifact.read_text(encoding="utf-8") == "<html>first</html>\n"
    assert _tree_entries(paths.target_root(tmp_artifact_root)) == (
        EXPECTED_HTML_NAME,
    )


def test_publish_artifact_file_accepts_a_binary_stream(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """``binary=True`` yields a byte stream, as the plain builtin's ``"wb"`` does.

    The two HTML artifacts are text, but the parameter exists so that a caller
    with bytes in hand - an inlined asset, a screenshot - is not forced to
    decode them merely to publish them.
    """
    artifact = paths.target_root(tmp_artifact_root) / "bytes.bin"

    with paths.publish_artifact_file(artifact, binary=True) as stream:
        stream.write(b"\x89PNG\r\n\x1a\n")

    assert artifact.read_bytes() == b"\x89PNG\r\n\x1a\n"


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be refused",
)
def test_publish_artifact_file_refuses_a_symlinked_destination(
    tmp_artifact_root: Path, tmp_path: Path, either_branch: bool
) -> None:
    """A link standing in for the artifact is refused, and its target survives.

    The publication is the case a symlink check most easily misses: the write
    itself lands in a temporary, so a link at the *destination* only matters at
    the rename - where a rename would replace the link rather than follow it,
    but the entry is still a file elsewhere and the artifact would go missing.
    Refused before anything is written, with the outside file asserted intact.
    """
    victim = _write(tmp_path / VICTIM_NAME, VICTIM_CONTENT)
    paths.ensure_dir(paths.target_root(tmp_artifact_root))
    artifact = paths.cucumber_reports_html_path(tmp_artifact_root)
    artifact.symlink_to(victim)

    with pytest.raises(paths.ArtifactPathError) as failure:
        with paths.publish_artifact_file(artifact) as stream:
            stream.write("<html></html>")
    assert EXPECTED_HTML_NAME in str(failure.value)
    assert victim.read_text(encoding="utf-8") == VICTIM_CONTENT


@pytest.mark.skipif(
    not HARD_LINKS_AVAILABLE,
    reason="this platform cannot create hard links, so none can be refused",
)
def test_publish_artifact_file_refuses_a_hard_linked_destination(
    tmp_artifact_root: Path, tmp_path: Path, either_branch: bool
) -> None:
    """An artifact hard-linked to a file outside the root is refused.

    No symbolic link exists anywhere in this path: the entry *is* the outside
    file, so the link count is the only thing that can tell, and a publication
    that renamed over it would make the outside file disappear from its own
    directory's point of view.
    """
    victim = _write(tmp_path / VICTIM_NAME, VICTIM_CONTENT)
    paths.ensure_dir(paths.target_root(tmp_artifact_root))
    artifact = paths.cucumber_reports_html_path(tmp_artifact_root)
    os.link(victim, artifact)

    with pytest.raises(paths.ArtifactPathError) as failure:
        with paths.publish_artifact_file(artifact) as stream:
            stream.write("<html></html>")
    assert "hard link" in str(failure.value)
    assert victim.read_text(encoding="utf-8") == VICTIM_CONTENT


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be refused",
)
def test_publish_artifact_file_refuses_a_symlinked_owned_component(
    tmp_artifact_root: Path, tmp_path: Path, either_branch: bool
) -> None:
    """A link standing in for ``target/`` is refused, and nothing is written there.

    The review's probe for this finding wrote the report *outside* the artifact
    root through exactly this shape, so the outside directory is asserted empty
    afterwards: a refusal that has already created the temporary in the link's
    destination is not a refusal.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    paths.target_root(tmp_artifact_root).symlink_to(outside, target_is_directory=True)

    with pytest.raises(paths.ArtifactPathError) as failure:
        with paths.publish_artifact_file(
            paths.cucumber_reports_html_path(tmp_artifact_root)
        ) as stream:
            stream.write("<html></html>")
    assert EXPECTED_TARGET_DIR in str(failure.value)
    assert _tree_entries(outside) == ()


def test_publish_artifact_file_refuses_a_parent_swapped_while_writing(
    tmp_artifact_root: Path, tmp_path: Path, without_no_follow_support: None
) -> None:
    """On the fallback branch a swapped parent fails the identity check.

    This is what the fallback binds instead of a descriptor.  The swap is made
    deterministically, inside the caller's own block, which is the window a
    pathname-based publication leaves open: without the binding the rename
    would resolve ``target/`` afresh and deposit the artifact in whatever
    directory now answers to that name.
    """
    decoy = tmp_path / "decoy"
    decoy.mkdir(exist_ok=True)
    artifact = paths.cucumber_reports_html_path(tmp_artifact_root)
    target = paths.ensure_dir(paths.target_root(tmp_artifact_root))

    with pytest.raises(paths.ArtifactPathError) as failure:
        with paths.publish_artifact_file(artifact) as stream:
            stream.write("<html></html>")
            os.rename(target, tmp_path / "stashed-target")
            os.rename(decoy, target)
    assert "changed while it was being used" in str(failure.value)
    assert _tree_entries(target) == ()


def test_publish_artifact_file_refuses_an_unbindable_component(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch,
    without_no_follow_support: None,
) -> None:
    """Where identity is unobtainable the fallback fails closed.

    A filesystem reporting ``st_ino == 0`` - some network shares, some FAT
    volumes - offers nothing to bind, so the alternative to refusing is
    proceeding on a name alone, which is the defect the binding replaces.
    Driven through :func:`os.lstat`, the call the owner makes, because no such
    filesystem can be mounted from a test.
    """
    paths.ensure_dir(paths.target_root(tmp_artifact_root))
    target = paths.target_root(tmp_artifact_root)
    real_lstat = os.lstat

    def anonymous(path: Any, *rest: Any, **options: Any) -> os.stat_result:
        info = real_lstat(path, *rest, **options)
        if str(path) == str(target):
            fields = list(tuple(info)[:10])
            fields[1] = 0
            return os.stat_result(tuple(fields))
        return info

    monkeypatch.setattr(os, "lstat", anonymous)

    with pytest.raises(paths.ArtifactPathError) as failure:
        with paths.publish_artifact_file(
            paths.cucumber_reports_html_path(tmp_artifact_root)
        ) as stream:
            stream.write("<html></html>")
    assert "no identity" in str(failure.value)


def test_a_reparse_point_is_refused_where_a_symlink_check_would_pass(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch,
    without_no_follow_support: None,
) -> None:
    """A junction is refused, which is the shape ``is_symlink()`` answers False for.

    A Windows junction is a directory reparse point that
    :meth:`~pathlib.Path.is_symlink` reports as no link at all while
    :func:`os.stat` follows it, so a fallback written against ``S_ISLNK``
    alone lets one redirect a write out of the artifact root.  No junction can
    be created on a POSIX host, so the platform's *report* of one is
    reproduced through :func:`os.lstat` - the owner's own call - and the
    refusal is the owner's.
    """
    target = paths.ensure_dir(paths.target_root(tmp_artifact_root))
    real_lstat = os.lstat

    class _Junction:
        """A stat report carrying the reparse attributes POSIX never sets."""

        def __init__(self, info: os.stat_result) -> None:
            self.st_mode = info.st_mode
            self.st_ino = info.st_ino
            self.st_dev = info.st_dev
            self.st_nlink = info.st_nlink
            self.st_mtime = info.st_mtime
            self.st_file_attributes = 0x400
            self.st_reparse_tag = 0xA000_0003

    def reparse(path: Any, *rest: Any, **options: Any) -> Any:
        info = real_lstat(path, *rest, **options)
        if str(path) == str(target):
            return _Junction(info)
        return info

    monkeypatch.setattr(os, "lstat", reparse)

    with pytest.raises(paths.ArtifactPathError) as failure:
        with paths.publish_artifact_file(
            paths.cucumber_reports_html_path(tmp_artifact_root)
        ) as stream:
            stream.write("<html></html>")
    assert EXPECTED_TARGET_DIR in str(failure.value)
    assert "symbolic link" in str(failure.value)


# ==========================================================================
# Section 15 - Directory publication
#
# Contract: :class:`app.utils.paths.ArtifactDirectoryPublication` and
# :func:`app.utils.paths.begin_directory_publication`.  The fourth artifact is
# a *directory*, so no single atomic write can publish it: the tree is built in
# a dot-prefixed staging sibling and swapped in by rename.  Every step -
# staging, page write, asset copy, both renames and the scratch removal - runs
# against the descriptor verified when the publication began, which is what a
# path-based staging cannot offer: the review's probe redirected a
# path-resolved cleanup into a prepared directory outside the artifact root.
# ==========================================================================


#: A page and an asset name, in the two shapes the PrettyReports tree uses: a
#: file at the root of the tree, and one in a sub-directory the publication has
#: to create on the way down.
PUBLISHED_PAGE: Final[str] = EXPECTED_PRETTY_INDEX
PUBLISHED_ASSET: Final[str] = "css/cucumber.css"

#: Relative names a publication must refuse outright, with the reason each one
#: is refused.  They come from the writer's own inventory rather than from a
#: request, so this guards a construction mistake - but it is the guard that
#: keeps a publication inside its staging tree, so it refuses instead of
#: normalising.
UNUSABLE_STAGING_NAMES: Final[tuple[str, ...]] = (
    "",
    "/absolute.html",
    "c:/drive.html",
    "../escape.html",
    "css/../../escape.html",
    "css//doubled.html",
    ".",
)


def _publish_one_page(
    final: Path, body: str = "<html>1</html>", *, pid: int | None = None
) -> Path:
    """Publish a one-page tree at ``final`` through the owner's own API.

    Used to prepare a *previous generation* for the tests that assert what
    happens to it, so the state under test is produced by production code
    rather than by a hand-built directory.

    :param final: The published tree's directory.
    :param body: Content of the overview page.
    :param pid: Process id the scratch names carry.
    :returns: The published directory.
    """
    with paths.begin_directory_publication(final, pid=pid) as publication:
        publication.create_staging()
        with publication.open(PUBLISHED_PAGE) as page:
            page.write(body)
        return publication.publish()


def test_a_publication_builds_in_staging_and_swaps_the_tree_into_place(
    tmp_artifact_root: Path, tmp_path: Path, either_branch: bool
) -> None:
    """The whole tree appears at once, and no scratch outlives the call.

    Asserts the sequence end to end: the staging tree is a dot-prefixed
    sibling while it is being built - unservable, because
    :func:`~app.utils.paths.resolve_artifact` rejects a dot-prefixed component
    - the pages and assets land in it, and the swap makes the complete tree
    visible under the published name in one rename.
    """
    source = _write(tmp_path / "cucumber.css", "body{}")
    final = paths.pretty_reports_html_dir(tmp_artifact_root)

    with paths.begin_directory_publication(final) as publication:
        assert publication.published_exists() is False
        staging = publication.create_staging()
        assert staging.name.startswith(".")
        assert paths.PUBLICATION_STAGING_INFIX in staging.name
        assert staging.is_dir()
        with publication.open(PUBLISHED_PAGE) as page:
            page.write("<html>overview</html>")
        copied = publication.copy_in(source, PUBLISHED_ASSET)
        assert publication.has_file(PUBLISHED_PAGE) is True
        assert publication.has_file(PUBLISHED_ASSET) is True
        assert publication.has_file("report-feature_1.html") is False
        assert copied == staging / "css" / "cucumber.css"
        assert publication.publish() == final
        assert publication.published is True
        assert publication.moved_aside is False

    assert (final / PUBLISHED_PAGE).read_text(encoding="utf-8") == (
        "<html>overview</html>"
    )
    assert (final / "css" / "cucumber.css").read_text(encoding="utf-8") == "body{}"
    assert _tree_entries(paths.pretty_reports_dir(tmp_artifact_root)) == (
        EXPECTED_PRETTY_SUBDIR,
        f"{EXPECTED_PRETTY_SUBDIR}/css",
        f"{EXPECTED_PRETTY_SUBDIR}/css/cucumber.css",
        f"{EXPECTED_PRETTY_SUBDIR}/{PUBLISHED_PAGE}",
    )


@pytest.mark.skipif(
    not hasattr(os, "fchmod"),
    reason="this platform expresses permissions as ACLs, so the policy does not apply",
)
def test_a_published_tree_is_owner_only_including_a_copied_asset(
    tmp_artifact_root: Path, tmp_path: Path, either_branch: bool
) -> None:
    """Pages, copied assets and the directories holding them are owner-only.

    The copied asset is the case a metadata-preserving copy gets wrong: a
    vendored file ships ``0644`` in a wheel, and
    :meth:`~app.utils.paths.ArtifactDirectoryPublication.copy_in` copies the
    bytes only, so the mode comes from the policy rather than from the source.
    """
    source = _write(tmp_path / "cucumber.css", "body{}")
    os.chmod(source, 0o666)
    final = paths.pretty_reports_html_dir(tmp_artifact_root)

    with paths.begin_directory_publication(final) as publication:
        publication.create_staging()
        with publication.open(PUBLISHED_PAGE) as page:
            page.write("<html></html>")
        publication.copy_in(source, PUBLISHED_ASSET)
        publication.publish()

    _assert_owner_only(final / PUBLISHED_PAGE)
    _assert_owner_only(final / "css" / "cucumber.css")
    _assert_owner_only(final / "css")
    _assert_owner_only(final)
    _assert_owner_only(paths.pretty_reports_dir(tmp_artifact_root))


def test_a_second_publication_renames_the_previous_generation_aside(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """The previous tree is moved aside, not deleted, and then discarded.

    The two renames are what make the swap possible at all - renaming a
    directory onto an existing directory fails on POSIX and on Windows alike -
    and the order matters: between them the published tree is *absent* rather
    than partial, and the copy aside is the only complete generation there is
    until the second rename lands.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    _publish_one_page(final, "<html>first</html>")

    with paths.begin_directory_publication(final) as publication:
        publication.create_staging()
        with publication.open(PUBLISHED_PAGE) as page:
            page.write("<html>second</html>")
        publication.publish()
        assert publication.moved_aside is True
        assert publication.superseded.is_dir()
        assert (publication.superseded / PUBLISHED_PAGE).read_text(
            encoding="utf-8"
        ) == "<html>first</html>"
        publication.discard_scratch(publication.superseded.name)
        superseded_name = publication.superseded.name

    assert (final / PUBLISHED_PAGE).read_text(encoding="utf-8") == "<html>second</html>"
    published = (
        EXPECTED_PRETTY_SUBDIR,
        f"{EXPECTED_PRETTY_SUBDIR}/{PUBLISHED_PAGE}",
    )
    entries = _tree_entries(paths.pretty_reports_dir(tmp_artifact_root))
    if either_branch:
        # With the primitives the whole superseded subtree is removed
        # descriptor-relative, so nothing of it survives the call.
        assert entries == published
    else:
        # Without them the superseded tree is *detached* rather than walked:
        # disposing of it by name would mean listing a directory that could
        # have been substituted since it was approved, which is how a cleanup
        # deletes something outside the artifact tree.  What is left is
        # dot-prefixed - unservable, and not under either scratch prefix, so no
        # later publication restores or re-disposes of it - and ``--clean``
        # removes it with the rest of the build output.
        assert set(published) <= set(entries)
        abandoned = [name for name in entries if name.startswith(".")]
        assert abandoned, "the detached scratch should still be there by name"
        assert all(
            not name.startswith((f".{final.name}", superseded_name))
            for name in abandoned
        )
        assert paths.resolve_artifact(
            f"{EXPECTED_PRETTY_DIR}/{abandoned[0]}", tmp_artifact_root
        ) is None


def test_scratch_entries_tell_this_process_leavings_from_another_process(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """Each scratch entry is reported with what a caller needs to decide on it.

    The distinction is load-bearing rather than cosmetic: a renamed-aside tree
    may be the only complete generation in existence, and scratch carrying
    another process's id may belong to a publication that is still running, so
    a writer that swept everything it found would destroy a concurrent run's
    work.  The entries are reported; the decision stays with the writer.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    _publish_one_page(final, "<html>previous</html>", pid=999_001)
    foreign = final.with_name(
        f".{final.name}{paths.PUBLICATION_SUPERSEDED_INFIX}999002"
    )

    with paths.begin_directory_publication(final, pid=999_001) as publication:
        publication.create_staging()
        paths.ensure_dir(foreign)
        entries = {entry.name: entry for entry in publication.scratch_entries()}

        assert set(entries) == {publication.staging.name, foreign.name}
        own = entries[publication.staging.name]
        assert own.is_own is True
        assert own.is_superseded is False
        assert own.is_dir is True
        assert own.path == publication.staging
        assert own.modified_at > 0
        other = entries[foreign.name]
        assert other.is_own is False
        assert other.is_superseded is True
        publication.discard_scratch(publication.staging.name)

    # The foreign entry is still there: nothing in the owner removes another
    # process's scratch, and ``--clean`` is what eventually does.
    assert foreign.is_dir()


def test_restore_superseded_puts_an_interrupted_publication_back(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """A tree left renamed aside is restored onto the published name.

    This is the recovery that makes the two-rename swap survivable: a process
    killed between the renames leaves no published tree and one renamed-aside
    copy, and without this the next run would start from nothing and a reader
    would have lost a complete generation to an unrelated crash.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    _publish_one_page(final, "<html>complete</html>", pid=999_003)
    abandoned = final.with_name(
        f".{final.name}{paths.PUBLICATION_SUPERSEDED_INFIX}999003"
    )
    os.rename(final, abandoned)

    with paths.begin_directory_publication(final, pid=999_003) as publication:
        assert publication.published_exists() is False
        names = [entry.name for entry in publication.scratch_entries()]
        assert names == [abandoned.name]
        publication.restore_superseded(abandoned.name)
        assert publication.published_exists() is True

    assert (final / PUBLISHED_PAGE).read_text(encoding="utf-8") == "<html>complete</html>"
    assert not abandoned.exists()


def test_a_publication_acts_only_on_scratch_names_it_recognises(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """Neither removal nor restoration accepts a name outside its own scratch.

    The published tree, a sibling artifact and a traversal spelling are all
    refused by name before any filesystem call is made, so a caller cannot ask
    a publication to delete or move something that is not its own - which is
    the whole reason the scratch names carry a fixed prefix.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    _publish_one_page(final)

    with paths.begin_directory_publication(final) as publication:
        for name in (
            final.name,
            EXPECTED_JSON_NAME,
            f"../{final.name}",
            f".{final.name}{paths.PUBLICATION_STAGING_INFIX}1/../../escape",
        ):
            with pytest.raises(paths.ArtifactPathError, match="scratch name"):
                publication.discard_scratch(name)
        # A staging name is scratch, but it is not a *renamed-aside* one, so
        # restoring it is refused as well: only a superseded tree is a
        # generation to restore.
        with pytest.raises(paths.ArtifactPathError, match="scratch name"):
            publication.restore_superseded(publication.staging.name)

    assert (final / PUBLISHED_PAGE).is_file()


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be refused",
)
def test_discarding_scratch_unlinks_a_planted_link_instead_of_following_it(
    tmp_artifact_root: Path, tmp_path: Path, either_branch: bool
) -> None:
    """A link inside the scratch is removed as a link; its target is untouched.

    The review's probe for this finding turned a path-resolved cleanup into a
    recursive delete of a prepared directory outside the artifact root.  The
    removal descends only through a descriptor opened on the entry itself, with
    ``O_NOFOLLOW``, so a link - a junction included - is unlinked rather than
    walked into.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    keep = _write(outside / "keep.txt", "KEEP")
    final = paths.pretty_reports_html_dir(tmp_artifact_root)

    with paths.begin_directory_publication(final) as publication:
        staging = publication.create_staging()
        with publication.open(PUBLISHED_PAGE) as page:
            page.write("<html></html>")
        (staging / "escape").symlink_to(outside, target_is_directory=True)
        publication.discard_scratch(staging.name)

        assert not staging.exists()

    assert keep.read_text(encoding="utf-8") == "KEEP"
    assert _tree_entries(outside) == ("keep.txt",)


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be refused",
)
def test_a_publication_refuses_a_link_where_the_published_tree_belongs(
    tmp_artifact_root: Path, tmp_path: Path, either_branch: bool
) -> None:
    """A link standing in for the tree is refused rather than renamed aside.

    The published tree is a directory this writer produced, so a link in its
    place is a redirection out of the artifact root - and renaming it aside and
    publishing over it would leave the redirection in the superseded copy and
    tell the operator nothing.  Refused before a staging tree is built, with
    the outside directory asserted untouched.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    paths.ensure_dir(final.parent)
    final.symlink_to(outside, target_is_directory=True)

    with paths.begin_directory_publication(final) as publication:
        with pytest.raises(paths.ArtifactPathError, match="symbolic link"):
            publication.published_exists()
        publication.create_staging()
        with publication.open(PUBLISHED_PAGE) as page:
            page.write("<html></html>")
        with pytest.raises(paths.ArtifactPathError, match="symbolic link"):
            publication.publish()
        publication.discard_scratch(publication.staging.name)

    assert _tree_entries(outside) == ()


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be refused",
)
def test_a_publication_refuses_a_symlinked_owned_component(
    tmp_artifact_root: Path, tmp_path: Path, either_branch: bool
) -> None:
    """A link standing in for ``target/`` fails before any page is rendered.

    ``begin_directory_publication`` verifies the tree's parent when it is
    created, which is deliberately early: the writer renders pages one at a
    time straight into staging, so a refusal that came at the first write would
    already have created a directory inside the link's destination.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    paths.target_root(tmp_artifact_root).symlink_to(outside, target_is_directory=True)

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.begin_directory_publication(
            paths.pretty_reports_html_dir(tmp_artifact_root)
        )
    assert EXPECTED_TARGET_DIR in str(failure.value)
    assert _tree_entries(outside) == ()


@pytest.mark.parametrize("name", UNUSABLE_STAGING_NAMES)
def test_a_staging_entry_name_that_could_escape_is_refused(
    name: str, tmp_artifact_root: Path, either_branch: bool
) -> None:
    """Every unusable relative name is refused, and nothing is created for it.

    Refusal rather than normalisation: a name the writer did not mean is a
    construction fault worth reporting, and a publication that silently
    rewrote one would make the emitted page set differ from the inventory the
    writer went on to verify.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)

    with paths.begin_directory_publication(final) as publication:
        staging = publication.create_staging()
        with pytest.raises(paths.ArtifactPathError):
            publication.open(name)
        assert _tree_entries(staging) == ()
        publication.discard_scratch(staging.name)


def test_a_publication_refuses_to_write_or_publish_before_staging_exists(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """Writing or publishing without a staging tree is refused, not improvised.

    A publication that created its staging directory implicitly on the first
    write would hide the one ordering the recovery step depends on: scratch
    left by an interrupted run is examined *before* a new staging tree is
    built, because building over it would publish a partial generation.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)

    with paths.begin_directory_publication(final) as publication:
        with pytest.raises(paths.ArtifactPathError, match="create_staging"):
            publication.open(PUBLISHED_PAGE)
        with pytest.raises(paths.ArtifactPathError, match="create_staging"):
            publication.has_file(PUBLISHED_PAGE)
        with pytest.raises(paths.ArtifactPathError, match="create_staging"):
            publication.publish()

    assert not final.exists()


def test_a_publication_cannot_be_published_twice(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """A second :meth:`publish` is refused rather than renaming the tree again.

    After the swap the staging name belongs to the published tree, so a second
    publish would rename the published artifact aside and leave nothing in its
    place.  The refusal is an :exc:`OSError`, so it reaches the caller's
    writer-failure handling with no new exception type (AAP 0.4.1).
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)

    with paths.begin_directory_publication(final) as publication:
        publication.create_staging()
        with publication.open(PUBLISHED_PAGE) as page:
            page.write("<html></html>")
        publication.publish()
        with pytest.raises(paths.ArtifactPathError, match="already been published"):
            publication.publish()

    assert (final / PUBLISHED_PAGE).is_file()
    assert isinstance(paths.ArtifactPathError("x"), OSError)


def test_closing_a_publication_releases_descriptors_and_removes_nothing(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """``close`` is idempotent and leaves every tree exactly where it was.

    What happens to a staging or renamed-aside tree after a failure is the
    writer's decision - it is the one case where the copy aside may be the only
    complete generation - so a context manager that swept scratch on the way
    out would take that decision away.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    publication = paths.begin_directory_publication(final)
    staging = publication.create_staging()
    with publication.open(PUBLISHED_PAGE) as page:
        page.write("<html></html>")

    publication.close()
    publication.close()

    assert staging.is_dir()
    assert (staging / PUBLISHED_PAGE).is_file()
    assert not final.exists()


def test_restore_superseded_reports_an_absent_or_unusable_scratch_tree(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """Restoring what is not a tree is reported rather than half-performed.

    Two shapes reach the same decision point and must not be conflated: a
    renamed-aside name that is not there at all - the ordinary case, a
    publication that completed and swept its scratch - and a *file* under a
    renamed-aside name, which is not a generation to publish. The first is an
    absence and the second a refusal, so a recovery step can tell "nothing to
    do" from "something is wrong here".
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)
    _publish_one_page(final, pid=999_004)
    absent = f".{final.name}{paths.PUBLICATION_SUPERSEDED_INFIX}999005"
    impostor = final.with_name(
        f".{final.name}{paths.PUBLICATION_SUPERSEDED_INFIX}999006"
    )
    _write(impostor, "not a tree")

    with paths.begin_directory_publication(final, pid=999_004) as publication:
        with pytest.raises(FileNotFoundError):
            publication.restore_superseded(absent)
        with pytest.raises(paths.ArtifactPathError, match="not a directory"):
            publication.restore_superseded(impostor.name)

    assert (final / PUBLISHED_PAGE).is_file()
    assert impostor.read_text(encoding="utf-8") == "not a tree"


def test_has_file_answers_false_for_a_missing_sub_directory(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """An inventory check for a page under a directory that was never created.

    The writer verifies its whole inventory over the staging tree before the
    swap, and an asset whose directory is missing is exactly what that check
    exists to catch - so the question must answer ``False`` rather than raise
    the platform's "no such directory" at the writer.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)

    with paths.begin_directory_publication(final) as publication:
        staging = publication.create_staging()
        assert publication.has_file("fonts/FontAwesome.otf") is False
        with publication.open(PUBLISHED_PAGE) as page:
            page.write("<html></html>")
        assert publication.has_file(f"{PUBLISHED_PAGE}/inner.html") is False
        publication.discard_scratch(staging.name)


def test_an_artifact_path_must_name_an_entry_on_either_branch(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """A path with no entry to verify is refused, not treated as a directory.

    ``ensure_parent`` and the write entry points verify a *final component*
    under its parent, so a bare filesystem root has nothing for them to check;
    answering it with a created directory would put an artifact somewhere no
    caller named.
    """
    root = Path(tmp_artifact_root.anchor)

    with pytest.raises(paths.ArtifactPathError, match="must name an entry"):
        paths.ensure_parent(root)


def test_publish_artifact_file_tolerates_a_caller_that_closed_the_stream(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """A stream the caller closed still publishes what was written to it.

    Closing through the interpreter has already flushed the bytes, so only the
    device sync is lost - and failing the publication over that would report a
    writer failure for an artifact that is complete and correct.
    """
    artifact = paths.cucumber_reports_html_path(tmp_artifact_root)

    with paths.publish_artifact_file(artifact) as stream:
        stream.write("<html>closed early</html>\n")
        stream.close()

    assert artifact.read_text(encoding="utf-8") == "<html>closed early</html>\n"


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be refused",
)
def test_a_staged_page_name_occupied_by_a_link_is_refused(
    tmp_artifact_root: Path, tmp_path: Path, either_branch: bool
) -> None:
    """A link planted at a page name inside staging is refused, not written through.

    The staging tree is dot-prefixed and short-lived, but it is not private:
    it sits in the build output, which survives a ``--no-clean`` run, so an
    entry inside it can be occupied before the writer gets there.  Writing a
    page through such a link would send a report page - screenshots and failure
    text included - wherever the link points.
    """
    victim = _write(tmp_path / VICTIM_NAME, VICTIM_CONTENT)
    final = paths.pretty_reports_html_dir(tmp_artifact_root)

    with paths.begin_directory_publication(final) as publication:
        staging = publication.create_staging()
        (staging / PUBLISHED_PAGE).symlink_to(victim)
        with pytest.raises(paths.ArtifactPathError, match="symbolic link"):
            publication.open(PUBLISHED_PAGE)
        assert publication.has_file(PUBLISHED_PAGE) is False
        publication.discard_scratch(staging.name)

    assert victim.read_text(encoding="utf-8") == VICTIM_CONTENT
    assert not final.exists()


def test_a_staged_page_may_sit_any_number_of_directories_deep(
    tmp_artifact_root: Path, either_branch: bool
) -> None:
    """Intermediate directories are created and verified one component at a time.

    The emitted asset set is one level deep today, so this is the property
    rather than the current shape: each component is created with the directory
    mode and opened under the descriptor of its already-verified parent, so a
    deeper name is neither refused nor resolved in one unchecked join.
    """
    final = paths.pretty_reports_html_dir(tmp_artifact_root)

    with paths.begin_directory_publication(final) as publication:
        publication.create_staging()
        with publication.open("a/b/c/page.html") as page:
            page.write("<html>deep</html>")
        assert publication.has_file("a/b/c/page.html") is True
        publication.publish()

    assert (final / "a" / "b" / "c" / "page.html").read_text(
        encoding="utf-8"
    ) == "<html>deep</html>"
    for depth in ("a", "a/b", "a/b/c"):
        _assert_owner_only(final.joinpath(*depth.split("/")))


# ==========================================================================
# Section 16 - The fallback's parent races, closed deterministically
#
# The fallback branch cannot hold a parent open, so what it must do instead is
# *detect* a substitution before anything crosses the boundary, and never
# perform an operation whose damage cannot be detected afterwards.  Each case
# below drives a real swap through the owner's own call - os.open, os.lstat -
# at a chosen point in the sequence, which is what makes it a test rather than
# a race: the window is opened deliberately rather than waited for.
# ==========================================================================


def _swapping_directories(
    trigger: str, first: Path, second: Path, *, restore: bool = False
) -> Callable[..., int]:
    """An :func:`os.open` replacement that exchanges two directories mid-open.

    :param trigger: The path whose open performs the swap.
    :param first: The directory moved out of the way - the artifact root.
    :param second: The directory moved into its place.
    :param restore: Whether to put ``first`` back before returning, which is
        the ABA case: the chain a re-stat sees afterwards is the chain that was
        bound, while the descriptor holds what ``second`` supplied.
    :returns: A drop-in replacement for :func:`os.open`.
    """
    real_open = os.open
    stash = first.with_name(f"{first.name}.stashed")

    def opening(path: Any, flags: int, *rest: Any, **options: Any) -> int:
        named = os.fspath(path) if isinstance(path, (str, os.PathLike)) else None
        if named != trigger:
            return real_open(path, flags, *rest, **options)
        os.rename(first, stash)
        os.rename(second, first)
        handle = real_open(path, flags, *rest, **options)
        if restore:
            os.rename(first, second)
            os.rename(stash, first)
        return handle

    return opening


@pytest.mark.parametrize("restore", [False, True], ids=["sustained", "aba"])
def test_fallback_refuses_a_read_whose_parent_was_substituted(
    restore: bool,
    tmp_artifact_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    without_no_follow_support: None,
) -> None:
    """A directory swapped around the open discloses nothing, either way round.

    Two shapes, one refusal.  A **sustained** swap leaves the substitute in
    place, so the name and the object it opened agree - on the attacker's file -
    and only the bound chain can tell; an **ABA** swap puts the original back
    before the checks run, so the chain agrees and only the object-to-name
    comparison can tell.  Both are made before the stream is handed back, so
    nothing is read from the substituted file in either case.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    _write(outside / EXPECTED_JSON_NAME, '{"external": "secret"}')
    artifact = _write(paths.cucumber_json_path(tmp_artifact_root), VICTIM_CONTENT)
    monkeypatch.setattr(
        os,
        "open",
        _swapping_directories(
            str(artifact),
            paths.target_root(tmp_artifact_root),
            outside,
            restore=restore,
        ),
    )

    with pytest.raises(paths.ArtifactPathError) as failure:
        paths.read_artifact_text(artifact)
    assert "changed while it was being" in str(failure.value)


@pytest.mark.parametrize("restore", [False, True], ids=["sustained", "aba"])
def test_fallback_refuses_a_write_whose_parent_was_substituted(
    restore: bool,
    tmp_artifact_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    without_no_follow_support: None,
) -> None:
    """The same two shapes on the write side leave the outside file untouched.

    The write is the destructive direction, so the assertion is on the bytes:
    the external file must still hold what it held, and the artifact the run
    was rewriting must still hold the previous run's content, because the
    truncation happens after both checks.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    victim = _write(outside / EXPECTED_JSON_NAME, VICTIM_CONTENT)
    artifact = _write(paths.cucumber_json_path(tmp_artifact_root), "[0]")
    monkeypatch.setattr(
        os,
        "open",
        _swapping_directories(
            str(artifact),
            paths.target_root(tmp_artifact_root),
            outside,
            restore=restore,
        ),
    )

    with pytest.raises(paths.ArtifactPathError):
        with paths.open_artifact_write(artifact) as stream:
            stream.write("[1]")
    # The sustained swap leaves the substitute standing where the artifact root
    # was, so the external file is looked up wherever the swap left it: what
    # matters is that its bytes are the bytes it started with, because the
    # truncation happens only after both checks have passed.
    relocated = paths.target_root(tmp_artifact_root) / EXPECTED_JSON_NAME
    survivor = victim if victim.exists() else relocated
    assert survivor.read_text(encoding="utf-8") == VICTIM_CONTENT


def _substituting_after_the_bind(
    root: Path, replacement: Path
) -> Callable[..., os.stat_result]:
    """An :func:`os.lstat` replacement that swaps the artifact root once bound.

    The swap is performed immediately after the first examination of the root -
    which is the owner binding the chain, before it resolves anything - and the
    identity handed back is the genuine root's, so what was bound is what was
    really there and what every later check sees is the substitute.  That is
    the exact window the review's probe exploited.

    :param root: The artifact root, moved aside.
    :param replacement: The directory moved into its place, and left there.
    :returns: A drop-in replacement for :func:`os.lstat`.
    """
    real_lstat = os.lstat
    examinations = {"root": 0}
    stash = root.with_name(f"{root.name}.stashed")

    def examining(path: Any, *rest: Any, **options: Any) -> os.stat_result:
        info = real_lstat(path, *rest, **options)
        named = os.fspath(path) if isinstance(path, (str, os.PathLike)) else None
        if named == str(root):
            examinations["root"] += 1
            if examinations["root"] == 1:
                os.rename(root, stash)
                os.rename(replacement, root)
        return info

    return examining


@pytest.mark.parametrize(
    "entry_point", ["resolve_artifact", "open_resolved_artifact"]
)
def test_fallback_rejects_a_request_whose_parent_was_substituted(
    entry_point: str,
    tmp_artifact_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    without_no_follow_support: None,
) -> None:
    """Both request entry points reject a root substituted after they bound it.

    Each binds the owned chain **before** it resolves the name and checks
    containment, and re-establishes it afterwards - the validator before it
    returns a path, the opener once the file is open - so a directory moved
    into the root's place inside that window cannot be served under an
    artifact's name.  A substitute that is itself a plain directory passes
    resolution and containment, which is why the identity comparison is the
    check that has to catch it.  Neither entry point raises: every rejection is
    ``None``.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    _write(outside / EXPECTED_JSON_NAME, '{"external": "secret"}')
    _write(paths.cucumber_json_path(tmp_artifact_root), VICTIM_CONTENT)
    monkeypatch.setattr(
        os,
        "lstat",
        _substituting_after_the_bind(paths.target_root(tmp_artifact_root), outside),
    )

    assert getattr(paths, entry_point)(EXPECTED_JSON_NAME, tmp_artifact_root) is None


def _substituting_a_directory_for_a_link(
    subject: Path, replacement: Path
) -> Callable[..., os.stat_result]:
    """An :func:`os.lstat` replacement that links ``subject`` away once examined.

    The identity handed back is the genuine directory's, so a caller that
    stats a name and then operates on that name again - the shape a recursive
    removal by pathname has to take on this branch - is told it approved a
    directory while the name has already become a link to somewhere else.
    That is precisely the window the review's cleanup probe exploited.

    :param subject: The directory whose examination performs the substitution.
    :param replacement: The directory the link is made to point at.
    :returns: A drop-in replacement for :func:`os.lstat`.
    """
    real_lstat = os.lstat
    substituted = {"done": False}

    def examining(path: Any, *rest: Any, **options: Any) -> os.stat_result:
        info = real_lstat(path, *rest, **options)
        named = os.fspath(path) if isinstance(path, (str, os.PathLike)) else None
        if named == str(subject) and not substituted["done"]:
            substituted["done"] = True
            os.rename(subject, subject.with_name(f"{subject.name}.moved"))
            subject.symlink_to(replacement, target_is_directory=True)
        return info

    return examining


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create symbolic links, so none can be substituted",
)
def test_fallback_scratch_disposal_never_descends_a_directory(
    tmp_artifact_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    without_no_follow_support: None,
) -> None:
    """Disposal cannot delete outside the tree, swapped at the worst moment.

    The review's probe replaced an already-approved staging directory with a
    link to a prepared directory outside the artifact root, in the window
    between the stat that approved it and the listing that walked it, and the
    cleanup followed the link and deleted the prepared directory's contents.
    Here the substitution is driven through the owner's own stat call, so the
    window is opened deliberately at exactly that point - and there is no walk
    left to redirect: a directory that still has contents is renamed aside
    rather than listed, so the prepared directory keeps every file it had
    whatever the scratch name points at by the time disposal runs.

    Whether the disposal reports an error is not the property under test - the
    entry stopped being what it was, so either refusing or disposing of the
    link itself is honest.  What must hold is that nothing outside the artifact
    root was touched.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir(exist_ok=True)
    keep = _write(outside / "keep.txt", "KEEP")
    final = paths.pretty_reports_html_dir(tmp_artifact_root)

    with paths.begin_directory_publication(final) as publication:
        staging = publication.create_staging()
        with publication.open(PUBLISHED_PAGE) as page:
            page.write("<html></html>")
        monkeypatch.setattr(
            os, "lstat", _substituting_a_directory_for_a_link(staging, outside)
        )
        with contextlib.suppress(OSError):
            publication.discard_scratch(staging.name)

    assert keep.read_text(encoding="utf-8") == "KEEP"
    assert _tree_entries(outside) == ("keep.txt",)
