"""Unit suite over the application factory: the Flask port of the pipeline's ``node { }``.

Subject under test
==================
:func:`app.create_app`. The source repository's only orchestration was a Groovy *scripted*
Jenkins pipeline of 512 bytes and seventeen physical lines whose outermost construct is an
executor block ``[Jenkins:L1-L17]``. Line 1 reads exactly::

    node {

and line 17 reads exactly::

    }

That container allocates an executor and *holds* the three stages -- ``'Clone code'``
``[Jenkins:L2-L4]``, ``'Run tests'`` ``[Jenkins:L6-L11]`` and ``'Generate report'``
``[Jenkins:L13-L15]`` -- while performing no work of its own. Its Python analogue is the
application factory, so this module asserts what the factory *holds and wires* and never what
a stage *does*: Agent Action Plan (AAP) Rule T2 is "One source construct, one target module",
and the three stages port to the three service modules whose own suites own their behaviour.

An irony worth recording, because it is the reason this file exists at all. The rewrite
request named "this Node.js server", yet no Node.js artifact has ever existed in this
repository -- no ``package.json``, no ``node_modules``, no ``.js``, ``.mjs``, ``.cjs`` or
``.ts`` file, on any branch, at any point in its history. The only artifact in the tree whose
content opens with the token ``node`` is the Groovy ``node { ... }`` above ``[Jenkins:L1]``,
which AAP AMB-1 records as the most probable referent, as an inference rather than a fact. The
construct this module's subject ports is therefore the closest thing to a "Node server" the
project ever had.

What is asserted here, and what deliberately is not
===================================================
Four contracts, one per AAP requirement, and nothing beyond them:

1. **Isolation** (enterprise baseline B3). Every call returns a fresh application and there is
   no module-level global. AAP section 0.4.2 names the failure a global would cause: "without
   it, tests could not build isolated application instances, and fixtures would share mutable
   state across the parallel workers introduced by ``-n logical``". That parallelism is itself
   the port of ``<parallel>methods</parallel>`` with
   ``<useUnlimitedThreads>true</useUnlimitedThreads>`` ``[pom.xml:L22-L23]``, so isolation is
   a *parity* requirement rather than a preference.
2. **Blueprint registration**. ``api_bp`` at ``/api/v1``, ``web_bp`` at the root, the single
   additive ``GET /health`` rule on the application itself, and no third blueprint. AAP
   section 0.8 is binding: "No feature may be dropped, and none may be added."
3. **Error-handler scope** (baseline B8). The 404, 405 and 500 handlers are registered on the
   APPLICATION, never on a blueprint. AAP section 0.3.2 records the researched reason:
   "Blueprint-level 404 and 405 handlers are not invoked for invalid URLs, because a blueprint
   does not own a URL space. ... Had this been overlooked, unknown routes would have produced
   Flask's default HTML error page instead of the intended structured response." This is the
   single most easily missed guarantee in the file, so it is asserted twice -- structurally,
   through Flask's own handler registry, and behaviourally, through a real request.
4. **Wiring**. Configuration, logging, extension binding, ``target/`` delegation and hostile
   filesystem tolerance.

Out of scope on purpose. Behavioural coverage of every route belongs to
``tests/integration/test_api_routes.py``; the three pipeline stages belong to
``tests/integration/test_pipeline_service.py``; the exhaustive configuration defaults belong
to ``tests/unit/test_config.py``; the ``target/`` layout belongs to ``tests/unit/test_paths.py``.
The one deliberate exception is the cheap 404/405 client check in requirement 3, which is what
converts a structural claim about handler scope into evidence that the handlers actually fire.
No server is started, no port is bound and no outbound request is made.

Two kinds of test, and why the split matters
============================================
* **Source-inspection tests** are plain module-level functions. They read committed files with
  an explicit UTF-8 encoding and reason over the parsed syntax tree, so they need nothing but
  the standard library and run in any environment -- including one where Flask, or a sibling
  application module, is not yet installed or authored. Four of the most important structural
  guarantees in this file live here for exactly that reason.
* **Runtime tests** live in classes guarded by the :func:`factory` fixture. Building the
  application requires Flask *and* every module the two blueprint packages import, so the
  guard converts an unbuildable application into a documented ``SKIP`` instead of a cascade of
  errors. A session-scoped guard is instantiated before the function-scoped ``app`` and
  ``client`` fixtures, which is what makes the skip deterministic.

Syntax trees rather than text matching
======================================
The documented cross-agent contracts are expressed as greps, and two of them are booby
trapped when taken literally:

* Counting the call token ``print`` immediately followed by an opening parenthesis reports
  **4** hits in ``app/__init__.py``, a file containing no such call at all, because the words
  ``blueprint``, ``Blueprint`` and ``register_blueprint`` -- each of them followed by an
  opening parenthesis -- all end in those same five letters.
* The lowercase literal ``target`` appears in that file only inside a comment and a docstring,
  neither of which is executable.

Every structural assertion is therefore made over the parsed tree, which cannot be fooled by a
substring, with the documented text form kept alongside as a corroborating check where it is
sound. Comments and docstrings are invisible to the parser, which is precisely the property
that makes it the right tool.

Fixtures used
=============
``project_root``, ``app``, ``client`` and ``caplog``. The first three come from
``tests/conftest.py``; ``project_root`` is session-scoped and derived from ``__file__``, never
from the working directory, because every ``-n logical`` worker is a separate process whose
working directory is not guaranteed. No application context is ever pushed here: leaking one
between tests is the shared-mutable-state hazard baseline B3 exists to prevent. There is no
``tests/unit/conftest.py`` and there must not be one.

Provenance
==========
``Jenkins`` and ``README.md`` are this module's source files. Nothing is reproduced from
either beyond the two container lines quoted above and the clone URL, which is read back out
of ``Jenkins`` at run time rather than restated -- so the spot-check cannot drift from the
pipeline it cites. ``pom.xml`` is a read-only parity contract (AAP AMB-4): it is cited, never
compiled, never edited, and no Java toolchain is required to run this suite (Rule T6).
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    # Annotation-only imports. `from __future__ import annotations` keeps every annotation a
    # string, so this block never executes: the source-inspection tests below stay runnable
    # with Flask absent while `mypy` still sees the real types at zero run-time cost.
    from collections.abc import Callable, Sequence

    from flask import Flask
    from flask.testing import FlaskClient

# ---------------------------------------------------------------------------------------
# Committed files this module inspects, as repository-root-relative POSIX paths composed
# onto the `project_root` fixture. Never composed onto the working directory: the suite runs
# one worker per logical CPU and no worker is guaranteed to have been started from the root.
# ---------------------------------------------------------------------------------------

FACTORY_MODULE: Final[str] = "app/__init__.py"
"""The subject under test: the application factory module."""

APPLICATION_PACKAGE: Final[str] = "app"
"""The deployable package. Nothing beneath it may import from ``tests`` (Rule T7)."""

WSGI_MODULE: Final[str] = "wsgi.py"
"""The gunicorn entrypoint -- the ONLY module allowed to hold an application at module scope."""

PIPELINE_DEFINITION: Final[str] = "Jenkins"
"""The Groovy scripted pipeline whose ``node { }`` container the factory ports."""

# ---------------------------------------------------------------------------------------
# The ported pipeline container, quoted from the source file so Rule T1 ("configuration
# values are data, not decisions") is executable rather than editorial.
# ---------------------------------------------------------------------------------------

PIPELINE_CONTAINER_OPEN: Final[str] = "node {"
"""``[Jenkins:L1]`` verbatim -- the executor block the application factory ports."""

PIPELINE_CONTAINER_CLOSE: Final[str] = "}"
"""``[Jenkins:L17]`` verbatim -- the close of that block."""

PIPELINE_STAGE_NAMES: Final[tuple[str, ...]] = ("Clone code", "Run tests", "Generate report")
"""The three stage names the container holds ``[Jenkins:L2, L6, L14]``, in pipeline order."""

CLONE_STEP_LINE_NUMBER: Final[int] = 3
"""One-based line of the ``git '<url>'`` step ``[Jenkins:L3]``, the configured clone default."""

_CLONE_URL_RE: Final[re.Pattern[str]] = re.compile(r"'(?P<url>[^']+)'")
"""Extracts the single-quoted URL from the Groovy ``git`` step, rather than restating it."""

# ---------------------------------------------------------------------------------------
# The HTTP surface, closed at the rules AAP section 0.3.1 enumerates. Every entry traces to
# a pipeline stage or a report artifact; only `/health` is additive, and it exists because a
# deployable service needs a liveness probe.
# ---------------------------------------------------------------------------------------

API_URL_PREFIX: Final[str] = "/api/v1"
"""Mount point declared once, by ``app/api/__init__.py``, and never re-specified."""

API_BLUEPRINT_NAME: Final[str] = "api"
"""The name given to the API ``Blueprint`` -- also its ``url_for`` namespace and registry key."""

WEB_BLUEPRINT_NAME: Final[str] = "web"
"""The name given to the web ``Blueprint``, root-mounted so its rules sit at ``/`` and ``/reports``."""

EXPECTED_BLUEPRINTS: Final[tuple[str, ...]] = (API_BLUEPRINT_NAME, WEB_BLUEPRINT_NAME)
"""Exactly two. ``/health`` lives on the application object, not on a third blueprint."""

HEALTH_RULE: Final[str] = "/health"
"""The one ADDITIVE rule in the whole surface -- "no source equivalent" (AAP 0.3.1)."""

HEALTH_ENDPOINT: Final[str] = "health"
"""Unqualified by any blueprint name, which is how a test proves it sits on the application."""

WEB_RULES: Final[tuple[str, ...]] = ("/", "/reports")
"""The rendered report surface ``[README.md:L152-L161]``, root-mounted."""

API_RULES: Final[tuple[str, ...]] = (
    f"{API_URL_PREFIX}/config",
    f"{API_URL_PREFIX}/clone",
    f"{API_URL_PREFIX}/runs",
    f"{API_URL_PREFIX}/runs/<run_id>",
    f"{API_URL_PREFIX}/reports",
    f"{API_URL_PREFIX}/reports/<run_id>/cucumber.json",
    f"{API_URL_PREFIX}/reports/<run_id>/rerun.txt",
    f"{API_URL_PREFIX}/reports/<run_id>/screenshots",
)
"""Configuration introspection, the three ported stages, run status and the three artifacts."""

FLASK_STATIC_RULE: Final[str] = "/static/<filename>"
"""Flask's automatic static rule, normalised. How ``app/static/css/main.css`` is served."""

DOCUMENTED_RULES: Final[frozenset[str]] = frozenset(
    (HEALTH_RULE, *API_RULES, *WEB_RULES, FLASK_STATIC_RULE)
)
"""The complete, CLOSED surface. An eleventh application rule would invent capability."""

_CONVERTER_RE: Final[re.Pattern[str]] = re.compile(r"<[^<>:]+:")
"""Strips a rule's converter prefix so ``<string:run_id>`` and ``<run_id>`` compare equal.

``app/__init__.py`` warns about precisely this: the two spellings "render as different rule
strings while routing identically", so an un-normalised comparison would fail on a difference
with no observable consequence.
"""

# ---------------------------------------------------------------------------------------
# Error handling. Statuses, the machine-readable codes and the envelope `app/errors.py`
# publishes, restated here as the expectation this suite holds the factory's wiring to.
# ---------------------------------------------------------------------------------------

HANDLED_STATUSES: Final[tuple[int, ...]] = (404, 405, 500)
"""The three statuses ``app/errors.py`` claims, all of them at application scope."""

APPLICATION_HANDLER_SCOPE: Final[None] = None
"""Flask files an application-scoped handler under the ``None`` key of the spec mapping."""

ERROR_ENVELOPE_KEYS: Final[frozenset[str]] = frozenset({"error", "message", "status", "details"})
"""The structured body served beneath ``/api/v1``, matching ``ApiErrorResponse``."""

NOT_FOUND_ERROR_CODE: Final[str] = "not_found"
"""Machine-readable code for a 404."""

METHOD_NOT_ALLOWED_ERROR_CODE: Final[str] = "method_not_allowed"
"""Machine-readable code for a 405."""

UNMATCHED_PATH: Final[str] = "/definitely-not-a-route-xyz"
"""A path no rule can match, used to prove the not-found handler really fires."""

POST_ONLY_API_PATH: Final[str] = f"{API_URL_PREFIX}/clone"
"""An existing rule that rejects ``GET`` -- the cheapest way to provoke a genuine 405."""

RENDERED_LAYOUT_MARKER: Final[bytes] = b"css/main.css"
"""Present only when this project's ``base.html`` rendered; absent from any framework page."""

FRAMEWORK_NOT_FOUND_MARKER: Final[bytes] = b"<title>404 Not Found</title>"
"""Flask's own default not-found page. Its presence would mean the custom handler never ran."""

HTML_MIMETYPE: Final[str] = "text/html"
"""What a non-API error must be answered with -- a rendered page, per baseline B8."""

JSON_MIMETYPE: Final[str] = "application/json"
"""What an error beneath ``/api/v1`` must be answered with -- the structured envelope."""

ALLOW_HEADER: Final[str] = "Allow"
"""RFC 9110 requires a 405 to name the methods the rule does accept."""

# ---------------------------------------------------------------------------------------
# Configuration profiles and keys, spelled exactly as `app/config.py` publishes them.
# ---------------------------------------------------------------------------------------

TESTING_CONFIG_NAME: Final[str] = "testing"
"""The profile ``tests/conftest.py`` builds. Only Flask's testing mode differs from the base."""

DEVELOPMENT_CONFIG_NAME: Final[str] = "development"
"""The documented default profile, used here to prove the selector actually selects."""

CONFIG_NAME_KEY: Final[str] = "CONFIG_NAME"
"""Records which profile an application was built with."""

CLONE_URL_KEY: Final[str] = "CLONE_URL"
"""Spot-checked against ``[Jenkins:L3]``; the exhaustive defaults belong to test_config.py."""

TARGET_DIR_KEY: Final[str] = "TARGET_DIR"
"""Re-bases the artifact root. The seam the hostile-filesystem test drives the factory through."""

TESTING_KEY: Final[str] = "TESTING"
"""Flask's own testing flag, the single difference between the testing and default profiles."""

ISOLATION_PROBE_KEY: Final[str] = "ISOLATION_PROBE"
"""A key no profile defines, written into one application to prove the next cannot see it."""

ISOLATION_PROBE_VALUE: Final[str] = "written-into-exactly-one-application"
"""The probe payload. Deliberately unmistakable in a failure message."""

# ---------------------------------------------------------------------------------------
# Structural expectations over `app/__init__.py`, and the documented grep contracts they
# correspond to.
# ---------------------------------------------------------------------------------------

FACTORY_FUNCTION_NAME: Final[str] = "create_app"
"""The single name the application package publishes."""

FACTORY_CONFIG_PARAMETER: Final[str] = "config_name"
"""The configuration selector, passed by keyword from ``wsgi.py``."""

BLUEPRINT_MODULES: Final[tuple[str, ...]] = ("app.api", "app.web")
"""Absolute module paths whose blueprint objects must be imported INSIDE the factory."""

RELATIVE_BLUEPRINT_MODULES: Final[tuple[str, ...]] = ("api", "web")
"""The relative spellings (``from .api import ...``) the same contract forbids at module scope."""

REQUIRED_FACTORY_CALLS: Final[tuple[str, ...]] = (
    "load_config",
    "configure_logging",
    "cors.init_app",
    "register_error_handlers",
    "register_blueprint",
    "add_url_rule",
)
"""Configuration, logging, extension binding, error handlers, blueprints, the health rule."""

DIRECTORY_CREATION_NAMES: Final[frozenset[str]] = frozenset(
    {"mkdir", "makedirs", "mkdtemp", "mkstemp", "mknod"}
)
"""Creating a directory here is forbidden: ``app/utils/paths.py`` is the single owner."""

PATHS_DELEGATE_MODULE: Final[str] = "app.utils.paths"
"""The named owner of the ``target/`` layout, which the factory must delegate to."""

ARTIFACT_ROOT_NAME: Final[str] = "target"
"""The Java-flavoured root name, retained so the publisher glob ``**/*.json`` needs no edit."""

FORBIDDEN_IMPORT_ROOT: Final[str] = "tests"
"""``tests`` may import ``app``; nothing under ``app`` may import ``tests`` (Rule T7 / B4)."""

_MODULE_LEVEL_APPLICATION_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:app|application)\s*[:=]", re.MULTILINE
)
"""The documented ``grep -nE '^(app|application)\\s*='`` contract, widened to catch the
annotated form ``app: Flask = ...`` that the bare grep would miss."""

_PRINT_CALL_RE: Final[re.Pattern[str]] = re.compile(r"(?<![A-Za-z0-9_.])print\s*\(")
"""Baseline B9's no-print contract. The lookbehind is what stops the tail of
``blueprint`` from matching, which a plain substring search cannot express."""

_TEST_TREE_IMPORT_RE: Final[re.Pattern[str]] = re.compile(
    rf"^\s*(?:from|import)\s+{FORBIDDEN_IMPORT_ROOT}\b", re.MULTILINE
)
"""The documented ``grep -rnE '^\\s*(from|import)\\s+tests' app/`` contract."""

_ARTIFACT_SUBDIRECTORY_RE: Final[re.Pattern[str]] = re.compile(rf"{ARTIFACT_ROOT_NAME}[/\\]\S")
"""Matches a hard-coded ``target/<something>`` path, which must come from the delegate only."""

_SKIP_TEMPLATE: Final[str] = (
    "Cannot build the application, so the run-time half of this suite is not assertable "
    "here. The structural assertions in this module ran regardless. Looked for {looked_for} "
    "and got {error_type}: {error}"
)
"""Skip reason template. Names what was looked for, so a skip is never a silent pass."""


# =======================================================================================
# Source-inspection helpers.
#
# Every one of them needs nothing but the standard library, which is what keeps the
# structural half of this suite runnable when Flask -- or a sibling application module that
# a blueprint package imports -- is unavailable. Reading is always explicitly UTF-8
# (baseline B7), and reasoning is always over the parsed syntax tree rather than over text,
# because comments and docstrings are invisible to the parser and therefore cannot produce a
# false match. Two documented grep contracts are booby trapped without that property: the
# call token `print` hides inside the word `blueprint`, and the literal `target` appears
# in the factory only inside prose.
# =======================================================================================


def _decoded_text(path: Path) -> str:
    """Return the full text of *path*, decoded as UTF-8.

    Args:
        path: Absolute path to a committed file.

    Returns:
        The file's contents. The encoding is stated rather than inherited from the platform
        default, so the same bytes are read on every host (baseline B7).
    """
    return path.read_text(encoding="utf-8")


def _parse_module(path: Path) -> ast.Module:
    """Parse *path* into a syntax tree.

    Args:
        path: Absolute path to a Python source file.

    Returns:
        The parsed module. A :class:`SyntaxError` is deliberately left to propagate: a
        committed module that cannot be parsed is a failure this suite must report, not
        tolerate.
    """
    return ast.parse(_decoded_text(path), filename=str(path))


def _docstring_constants(tree: ast.AST) -> frozenset[int]:
    """Return the identities of every string constant that is a docstring.

    Docstrings are prose, so a literal inside one carries no executable meaning and must not
    satisfy -- or violate -- an assertion about what the code does.

    Args:
        tree: Any parsed node; the whole tree is walked.

    Returns:
        ``id()`` values of the constant nodes occupying a docstring position in a module,
        class or function body.
    """
    identities: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            identities.add(id(first.value))
    return frozenset(identities)


def _code_string_literals(tree: ast.AST) -> tuple[str, ...]:
    """Return every string literal that is part of the code rather than of its prose.

    Args:
        tree: A parsed module.

    Returns:
        The string constants of *tree*, docstrings excluded, in walk order.
    """
    docstrings = _docstring_constants(tree)
    return tuple(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )


def _module_level_bindings(tree: ast.Module) -> tuple[tuple[str, ast.expr], ...]:
    """Return the names bound at module scope, paired with the expression bound to each.

    Only the module body is examined -- never a nested function or class -- because a name
    bound inside :func:`app.create_app` is exactly what the factory pattern *requires*, while
    the same name bound at module scope is what it forbids.

    Args:
        tree: A parsed module.

    Returns:
        ``(name, value)`` pairs for every plain and annotated assignment at module scope. An
        annotated declaration with no value (``app: Flask``) binds nothing and is omitted.
    """
    bindings: list[tuple[str, ast.expr]] = []
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            bindings.extend(
                (target.id, statement.value)
                for target in statement.targets
                if isinstance(target, ast.Name)
            )
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.value is not None
        ):
            bindings.append((statement.target.id, statement.value))
    return tuple(bindings)


def _function_definition(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    """Return the module-scope function called *name*, or ``None`` when it is absent.

    Args:
        tree: A parsed module.
        name: The function name to find.

    Returns:
        The matching definition, or ``None``. Returning ``None`` rather than raising lets the
        caller report a precise, actionable failure naming what it looked for.
    """
    for statement in tree.body:
        if isinstance(statement, ast.FunctionDef) and statement.name == name:
            return statement
    return None


def _scoped_nodes(nodes: Sequence[ast.stmt]) -> tuple[ast.AST, ...]:
    """Return every node belonging to the scope *nodes* opens, without entering a nested one.

    This is the distinction the whole blueprint-import contract turns on. ``ast.walk`` descends
    into function bodies, so walking a module would report an import made *inside*
    :func:`app.create_app` as a module-scope import -- which is the precise opposite of what
    AAP section 0.4.2 requires. Traversal therefore stops at every nested definition, while
    still entering conditionals, ``try`` blocks and ``with`` blocks, since an import placed in
    one of those is in the same scope as the statements around it.

    Args:
        nodes: The statements of one scope -- ``tree.body`` for a module, a function's ``body``
            for that function.

    Returns:
        The nodes of that scope, nested definitions included as nodes but not descended into.
    """
    collected: list[ast.AST] = []
    pending: list[ast.AST] = list(nodes)
    boundaries = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    while pending:
        node = pending.pop()
        collected.append(node)
        if isinstance(node, boundaries):
            continue
        pending.extend(ast.iter_child_nodes(node))
    return tuple(collected)


def _imported_modules(nodes: Sequence[ast.stmt]) -> frozenset[str]:
    """Return every module imported by the scope *nodes* opens.

    Args:
        nodes: The statements of one scope. Pass ``tree.body`` for module-scope imports, or a
            function's ``body`` for the imports made inside it; a nested definition's own
            imports belong to that definition and are excluded.

    Returns:
        Absolute module paths (``app.api``) and relative ones rendered with their leading dots
        (``.api``), so both spellings of the same contract can be checked.
    """
    modules: set[str] = set()
    for node in _scoped_nodes(nodes):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add("." * node.level + (node.module or ""))
    return frozenset(modules)


def _every_imported_module(tree: ast.AST) -> frozenset[str]:
    """Return every module imported anywhere in *tree*, at any nesting depth.

    Used where the contract is stated over a whole file rather than over one scope -- the
    one-direction dependency rule forbids an import from ``tests`` no matter how deeply it is
    buried.

    Args:
        tree: A parsed module.

    Returns:
        Every imported module path, absolute and relative alike.
    """
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add("." * node.level + (node.module or ""))
    return frozenset(modules)


def _dotted_name(node: ast.expr) -> str | None:
    """Render *node* as a dotted name when it is one.

    Args:
        node: The callee expression of a call.

    Returns:
        ``"cors.init_app"`` for an attribute chain, ``"load_config"`` for a bare name, or
        ``None`` when the expression is something else entirely -- a subscript or a call
        result, neither of which this module needs to reason about.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base is not None else node.attr
    return None


def _call_targets(node: ast.AST) -> frozenset[str]:
    """Return every callee named anywhere inside *node*.

    Both the full dotted form and the trailing attribute are reported, so a caller may assert
    ``"cors.init_app"`` precisely or ``"mkdir"`` however it was reached.

    Args:
        node: Any parsed node; the whole subtree is walked.

    Returns:
        The callee names found, full dotted forms and bare trailing names together.
    """
    targets: set[str] = set()
    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.Call):
            continue
        dotted = _dotted_name(candidate.func)
        if dotted is None:
            continue
        targets.add(dotted)
        targets.add(dotted.rpartition(".")[2])
    return frozenset(targets)


def _python_sources(package_root: Path) -> tuple[Path, ...]:
    """Return every committed Python module beneath *package_root*, sorted for determinism.

    Args:
        package_root: Directory to search recursively.

    Returns:
        The ``.py`` files found, with anything under a ``__pycache__`` directory excluded --
        compiled caches are build output, and a stale one is exactly what once made a
        verification in AAP section 0.6 appear to disprove itself.
    """
    return tuple(
        sorted(
            path
            for path in package_root.rglob("*.py")
            if "__pycache__" not in path.parts and path.is_file()
        )
    )


def _normalise_rule(rule: str) -> str:
    """Strip converter prefixes from a URL rule so equivalent spellings compare equal.

    Args:
        rule: A rule string as Werkzeug renders it.

    Returns:
        The rule with every ``<converter:name>`` reduced to ``<name>``, which makes
        ``/api/v1/runs/<string:run_id>`` and ``/api/v1/runs/<run_id>`` -- identical in
        routing behaviour -- identical here too.
    """
    return _CONVERTER_RE.sub("<", rule)


def _registered_rules(flask_app: Flask) -> frozenset[str]:
    """Return the application's registered URL rules, normalised.

    Args:
        flask_app: A built application.

    Returns:
        Every rule in ``flask_app.url_map``, converter prefixes stripped.
    """
    return frozenset(_normalise_rule(rule.rule) for rule in flask_app.url_map.iter_rules())


def _pipeline_lines(project_root: Path) -> tuple[str, ...]:
    """Return the pipeline definition split into physical lines.

    ``Jenkins`` carries no trailing newline, so splitting on line boundaries yields exactly
    the seventeen physical lines the file contains rather than an eighteenth empty one.

    Args:
        project_root: The repository root.

    Returns:
        The lines of ``Jenkins``, in file order.
    """
    return tuple(_decoded_text(project_root / PIPELINE_DEFINITION).splitlines())


def _configured_clone_url(project_root: Path) -> str:
    """Return the clone URL the pipeline's checkout stage names ``[Jenkins:L3]``.

    Read out of the source file rather than restated, so the configuration spot-check below
    cannot drift from the pipeline it claims parity with (baseline B11).

    Args:
        project_root: The repository root.

    Returns:
        The single-quoted URL from the ``git`` step of stage ``'Clone code'``.

    Raises:
        AssertionError: If that line no longer carries a quoted URL, which would mean the
            preserved checkout contract had been altered.
    """
    lines = _pipeline_lines(project_root)
    assert len(lines) >= CLONE_STEP_LINE_NUMBER, (
        f"{PIPELINE_DEFINITION} has only {len(lines)} lines, so line "
        f"{CLONE_STEP_LINE_NUMBER} -- the checkout step of stage 'Clone code' -- is missing"
    )
    step = lines[CLONE_STEP_LINE_NUMBER - 1]
    match = _CLONE_URL_RE.search(step)
    assert match is not None, (
        f"[{PIPELINE_DEFINITION}:L{CLONE_STEP_LINE_NUMBER}] no longer carries a quoted clone "
        f"URL; the line reads {step!r}"
    )
    return match.group("url")


# =======================================================================================
# STRUCTURAL ASSERTIONS -- no Flask, no application, no third-party package.
#
# These run in every environment, which is deliberate: in a partially provisioned one they
# are the only evidence available, and they happen to cover four of the most important
# guarantees in the whole file -- no module-level global, no directory creation, blueprint
# imports inside the factory, and no import from the test tree.
# =======================================================================================


def test_factory_module_binds_no_application_at_module_scope(project_root: Path) -> None:
    """Assert the factory module holds no global application (enterprise baseline B3).

    Baseline B3 asks for "application factory plus blueprints rather than a module-level
    global application", and AAP section 0.4.2 names the failure a global would cause:
    "without it, tests could not build isolated application instances, and fixtures would
    share mutable state across the parallel workers introduced by ``-n logical``". Since that
    parallelism ports ``<parallel>methods</parallel>`` with
    ``<useUnlimitedThreads>true</useUnlimitedThreads>`` ``[pom.xml:L22-L23]``, a global here
    would break behavioural parity rather than merely offend taste.

    Three checks, narrowing from the general to the documented:

    1. No name ``app`` or ``application`` is bound at module scope at all. The identical
       binding *inside* the factory is exactly what the pattern requires, and is not examined.
    2. No module-scope binding evaluates a call to ``Flask`` or to the factory itself, whatever
       it is named -- which catches ``_default_app = create_app()`` that the name check alone
       would miss.
    3. The documented text contract, ``grep -nE '^(app|application)\\s*='``, reports nothing.

    Args:
        project_root: The repository root, from ``tests/conftest.py``.
    """
    path = project_root / FACTORY_MODULE
    tree = _parse_module(path)
    bindings = _module_level_bindings(tree)

    reserved = {"app", "application"}
    offenders = [name for name, _ in bindings if name in reserved]
    assert not offenders, (
        f"{FACTORY_MODULE} binds {offenders} at module scope. An application built at import "
        f"time is shared by every test and every -n logical worker, which is precisely what "
        f"the application-factory pattern exists to eliminate (baseline B3, AAP 0.4.2). The "
        f"only module-level instance in the tree belongs to {WSGI_MODULE}."
    )

    constructors = {"Flask", FACTORY_FUNCTION_NAME}
    built = [name for name, value in bindings if _call_targets(value) & constructors]
    assert not built, (
        f"{FACTORY_MODULE} evaluates an application constructor at module scope for {built}. "
        f"Construction belongs inside {FACTORY_FUNCTION_NAME}(), so that importing the "
        f"package has no side effect whatsoever."
    )

    textual = _MODULE_LEVEL_APPLICATION_RE.findall(_decoded_text(path))
    assert not textual, (
        f"The documented contract grep -nE '^(app|application)\\s*=' {FACTORY_MODULE} matched "
        f"{textual}, so a module-level application assignment has been introduced."
    )


def test_factory_module_creates_no_directory_itself(project_root: Path) -> None:
    """Assert the factory delegates ``target/`` creation to ``app/utils/paths.py``.

    AAP section 0.6 records the silent failure this closes: "The Cucumber JSON writer does not
    create its parent directory. With ``target/`` absent, the run fails at session finish with
    a file-not-found error. Maven implicitly created ``target/``; the Python port must create
    it first. This requirement is assigned to three places so it cannot be missed:
    ``app/utils/paths.py``, the ``Makefile`` test target, and ``tests/conftest.py``."

    The factory is not one of those three, and the cross-agent contract is exact:
    ``grep -c 'mkdir' app/__init__.py`` must report ``0``. A fourth owner of the layout would
    diverge from the specification the moment the layout changed, so this asserts the contract
    from the consuming side and closes the loop.

    Args:
        project_root: The repository root, from ``tests/conftest.py``.
    """
    path = project_root / FACTORY_MODULE
    tree = _parse_module(path)

    creators = sorted(_call_targets(tree) & DIRECTORY_CREATION_NAMES)
    assert not creators, (
        f"{FACTORY_MODULE} calls {creators}. Directory creation belongs to "
        f"{PATHS_DELEGATE_MODULE}, the single named owner of the artifact layout (AAP 0.6, "
        f"validation criterion V7); a second owner is how the two silently drift apart."
    )

    source = _decoded_text(path)
    assert source.count("mkdir") == 0, (
        f"The cross-agent contract grep -c 'mkdir' {FACTORY_MODULE} must report 0, but the "
        f"text 'mkdir' occurs {source.count('mkdir')} time(s)."
    )

    delegated = _imported_modules(tree.body)
    assert PATHS_DELEGATE_MODULE in delegated, (
        f"{FACTORY_MODULE} must reach the artifact layout through {PATHS_DELEGATE_MODULE}, "
        f"but its module-scope imports are {sorted(delegated)}. Delegation is what makes the "
        f"'creates no directory itself' guarantee above a design rather than an omission."
    )


def test_factory_module_hard_codes_no_artifact_subdirectory(project_root: Path) -> None:
    """Assert no ``target/`` path is spelled out in the factory's executable code.

    The artifact root name is deliberately retained -- AAP section 0.3.1: the publisher glob
    ``fileIncludePattern: '**/*.json'`` ``[Jenkins:L15]`` needs no change, and "this is
    precisely why the Java-flavoured ``target/`` name is retained rather than renamed". It is
    retained in exactly one place, though: the literal must reach the factory through
    ``app/utils/paths.py`` constants, never as a string of its own.

    Only executable literals are examined. The word appears in this module's prose -- a
    comment about ``Path("target")`` and a docstring listing the five managed directories --
    and prose is not a hard-coded path, which is why a raw text search would report a false
    violation here and the parsed tree does not.

    Args:
        project_root: The repository root, from ``tests/conftest.py``.
    """
    tree = _parse_module(project_root / FACTORY_MODULE)
    literals = _code_string_literals(tree)

    subdirectories = [text for text in literals if _ARTIFACT_SUBDIRECTORY_RE.search(text)]
    assert not subdirectories, (
        f"{FACTORY_MODULE} hard-codes the artifact subdirectory path(s) {subdirectories}. "
        f"Every path beneath the artifact root is published by {PATHS_DELEGATE_MODULE}; "
        f"restating one here creates a second source of truth for the layout."
    )

    roots = [text for text in literals if text == ARTIFACT_ROOT_NAME]
    assert not roots, (
        f"{FACTORY_MODULE} spells the artifact root name {ARTIFACT_ROOT_NAME!r} as a literal. "
        f"It must arrive as a constant from {PATHS_DELEGATE_MODULE}, which owns the name and "
        f"keeps the Jenkins publisher glob valid without an edit."
    )


def test_blueprints_are_imported_inside_the_factory(project_root: Path) -> None:
    """Assert the blueprint imports sit inside the factory body, not at module scope.

    AAP section 0.4.2, verbatim: "Blueprint objects are imported inside ``create_app()``,
    after construction, to prevent circular imports." The mechanism is concrete rather than
    superstitious -- the blueprint packages import ``app.services``, which imports
    ``app.reporting`` and ``app.utils``, and any of those may be imported while
    ``app/__init__.py`` is still initialising. A module-scope import here would close that
    cycle straight back through the package being initialised.

    Both spellings of the forbidden import are checked, absolute and relative, because either
    would reintroduce the cycle.

    Args:
        project_root: The repository root, from ``tests/conftest.py``.
    """
    path = project_root / FACTORY_MODULE
    tree = _parse_module(path)

    forbidden = set(BLUEPRINT_MODULES) | {f".{name}" for name in RELATIVE_BLUEPRINT_MODULES}
    at_module_scope = sorted(_imported_modules(tree.body) & forbidden)
    assert not at_module_scope, (
        f"{FACTORY_MODULE} imports {at_module_scope} at module scope. AAP 0.4.2 requires the "
        f"blueprint objects to be imported inside {FACTORY_FUNCTION_NAME}(), after "
        f"construction, to prevent circular imports."
    )

    factory = _function_definition(tree, FACTORY_FUNCTION_NAME)
    assert factory is not None, (
        f"{FACTORY_MODULE} defines no module-scope {FACTORY_FUNCTION_NAME}(); it is the single "
        f"name the application package publishes."
    )

    inside = _imported_modules(factory.body)
    missing = [module for module in BLUEPRINT_MODULES if module not in inside]
    assert not missing, (
        f"{FACTORY_FUNCTION_NAME}() does not import {missing}. Both blueprints must be "
        f"imported inside the factory body and registered there: the API blueprint mounted at "
        f"{API_URL_PREFIX} and the web blueprint at the root. Its local imports are "
        f"{sorted(inside)}."
    )

    column_zero = re.compile(
        r"^(?:from|import)\s+(?:app\.)?(?:" + "|".join(RELATIVE_BLUEPRINT_MODULES) + r")\b",
        re.MULTILINE,
    )
    textual = column_zero.findall(_decoded_text(path))
    assert not textual, (
        f"An unindented blueprint import was found in {FACTORY_MODULE}: {textual}. The "
        f"documented contract is that no column-zero 'from app.api import' or "
        f"'from app.web import' statement exists, while an indented one does."
    )


def test_factory_module_contains_no_print_call(project_root: Path) -> None:
    """Assert the factory logs and never prints (enterprise baseline B9).

    Baseline B9 requires "structured logging rather than print statements, to a git-ignored
    path". Diagnostics belong to ``app/logging_config.py``, which is the only module allowed
    to install a handler; a stray ``print`` would bypass the configured format, the level and
    the secret redaction it applies, and would write to a stream nothing captures.

    The parsed tree is the authority here for a specific reason: the documented shorthand
    counting the call token ``print`` followed by an opening parenthesis reports a non-zero
    count for ``app/__init__.py``, a file with no such call at all, because ``blueprint``,
    ``Blueprint`` and ``register_blueprint`` all end in those five letters and are all
    called. The corroborating text check below therefore carries a lookbehind that a plain
    substring search cannot express.

    Args:
        project_root: The repository root, from ``tests/conftest.py``.
    """
    path = project_root / FACTORY_MODULE
    tree = _parse_module(path)

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]
    lines = sorted(node.lineno for node in calls)
    assert not calls, (
        f"{FACTORY_MODULE} calls the print builtin at line(s) {lines}. Baseline B9 requires "
        f"structured "
        f"logging to the git-ignored destination app/logging_config.py owns, which is also "
        f"where secret redaction is applied."
    )

    textual = _PRINT_CALL_RE.findall(_decoded_text(path))
    assert not textual, (
        f"A call to the print builtin survives in {FACTORY_MODULE}: {textual}. The pattern "
        f"carries a lookbehind, so a match here is a genuine call and not the tail of the "
        f"word blueprint, which is what makes a plain substring search report a false hit."
    )


def test_application_package_never_imports_from_the_test_tree(project_root: Path) -> None:
    """Assert nothing under ``app/`` imports from ``tests/`` (AAP Rule T7, baseline B4).

    Rule T7, verbatim: "Strict one-direction internal dependencies. ``api -> services ->
    reporting -> utils``. Nothing under ``app/`` may import from ``tests/``." The edge is what
    keeps the deployable application independent of the harness even though both derive from
    the same source specification -- and it is load-bearing at deployment time, because the
    container installs ``requirements.txt`` alone while the harness distributions live in
    ``requirements-test.txt``. An import across that edge would make ``import wsgi`` fail in
    production.

    Every module in the package is checked, not just the factory, because the contract is
    stated over the whole tree.

    Args:
        project_root: The repository root, from ``tests/conftest.py``.
    """
    package_root = project_root / APPLICATION_PACKAGE
    assert package_root.is_dir(), f"{APPLICATION_PACKAGE}/ is missing from {project_root}"

    modules = _python_sources(package_root)
    assert modules, f"{APPLICATION_PACKAGE}/ holds no Python module, so nothing was verified"

    violations: list[str] = []
    for path in modules:
        relative = path.relative_to(project_root).as_posix()
        tree = _parse_module(path)
        imported = _every_imported_module(tree)
        violations.extend(
            f"{relative} imports {module}"
            for module in sorted(imported)
            if module == FORBIDDEN_IMPORT_ROOT or module.startswith(f"{FORBIDDEN_IMPORT_ROOT}.")
        )
        violations.extend(
            f"{relative} matches the documented grep: {match!r}"
            for match in _TEST_TREE_IMPORT_RE.findall(_decoded_text(path))
        )

    assert not violations, (
        "The one-direction dependency rule is broken: "
        + "; ".join(violations)
        + f". tests/ may import {APPLICATION_PACKAGE}/; the reverse edge must not exist "
        "(Rule T7, baseline B4)."
    )


def test_factory_wires_configuration_logging_extensions_and_handlers(project_root: Path) -> None:
    """Assert the factory performs every wiring step AAP section 0.4.1 assigns to it.

    AAP section 0.4.1 describes ``app/__init__.py`` as the "Application factory wiring
    configuration, blueprints, error handlers, logging". Each of those is delegated to the
    module that owns it, and this test asserts the delegation is actually invoked -- a factory
    that imported ``configure_logging`` without calling it would satisfy an import check while
    leaving every diagnostic in the run unformatted.

    The extension binding is included because AAP section 0.3.2 records why the factory
    pattern was chosen at all: Flask "recommends the application-factory function so
    extensions are not bound to one application instance at import time". ``app/extensions.py``
    declares the singleton unbound; binding may only happen here.

    Two blueprint registrations are asserted, no more and no fewer: AAP section 0.8 forbids
    adding a feature, and the additive health rule goes on the application itself through
    ``add_url_rule`` rather than on a third blueprint.

    Args:
        project_root: The repository root, from ``tests/conftest.py``.
    """
    tree = _parse_module(project_root / FACTORY_MODULE)
    factory = _function_definition(tree, FACTORY_FUNCTION_NAME)
    assert factory is not None, f"{FACTORY_MODULE} defines no {FACTORY_FUNCTION_NAME}()"

    parameters = [argument.arg for argument in factory.args.args]
    assert FACTORY_CONFIG_PARAMETER in parameters, (
        f"{FACTORY_FUNCTION_NAME}({', '.join(parameters)}) does not accept "
        f"{FACTORY_CONFIG_PARAMETER!r}. The profile selector is part of the published "
        f"contract: {WSGI_MODULE} passes it by keyword."
    )

    invoked = _call_targets(factory)
    missing = [name for name in REQUIRED_FACTORY_CALLS if name not in invoked]
    assert not missing, (
        f"{FACTORY_FUNCTION_NAME}() never calls {missing}. Configuration, structured logging, "
        f"extension binding, the error handlers, the two blueprints and the additive health "
        f"rule are all wired by the factory and nowhere else (AAP 0.4.1)."
    )

    registrations = sum(
        1
        for node in ast.walk(factory)
        if isinstance(node, ast.Call) and _dotted_name(node.func) == "app.register_blueprint"
    )
    assert registrations == len(EXPECTED_BLUEPRINTS), (
        f"{FACTORY_FUNCTION_NAME}() performs {registrations} blueprint registration(s); "
        f"exactly {len(EXPECTED_BLUEPRINTS)} are expected, one per blueprint in "
        f"{list(EXPECTED_BLUEPRINTS)}. AAP 0.8: 'No feature may be dropped, and none may be "
        f"added.'"
    )


def test_only_the_wsgi_entrypoint_holds_an_application_at_module_scope(project_root: Path) -> None:
    """Assert the single module-level application in the tree belongs to ``wsgi.py``.

    The factory pattern does not forbid a module-level instance outright -- gunicorn resolves
    ``wsgi:app`` by attribute lookup, so exactly one has to exist. It forbids one *inside the
    package*, where every importer would share it. This test pins the instance to the one
    module entitled to hold it and asserts the package holds none.

    ``wsgi.py`` writes ``app: Flask = create_app(config_name=...)``, an annotated assignment,
    which the documented ``^(app|application)\\s*=`` grep does not match -- another reason the
    parsed tree rather than a text search is the authority.

    Args:
        project_root: The repository root, from ``tests/conftest.py``.
    """
    entrypoint = project_root / WSGI_MODULE
    if not entrypoint.is_file():
        pytest.skip(
            f"{WSGI_MODULE} is not present in this checkout, so the gunicorn entrypoint's "
            f"module-level application cannot be located. Looked for a module-scope binding "
            f"of 'app' whose value calls {FACTORY_FUNCTION_NAME}()."
        )

    bindings = _module_level_bindings(_parse_module(entrypoint))
    built = [
        name
        for name, value in bindings
        if name == "app" and FACTORY_FUNCTION_NAME in _call_targets(value)
    ]
    assert built, (
        f"{WSGI_MODULE} must bind a module-level 'app' from {FACTORY_FUNCTION_NAME}(): "
        f"gunicorn loads 'wsgi:app' by attribute lookup. Module-scope bindings found: "
        f"{[name for name, _ in bindings]}."
    )

    offenders: list[str] = []
    for path in _python_sources(project_root / APPLICATION_PACKAGE):
        relative = path.relative_to(project_root).as_posix()
        offenders.extend(
            f"{relative} binds {name}"
            for name, value in _module_level_bindings(_parse_module(path))
            if _call_targets(value) & {"Flask", FACTORY_FUNCTION_NAME}
        )

    assert not offenders, (
        "An application is built at import time inside the deployable package: "
        + "; ".join(offenders)
        + f". The module-level instance belongs to {WSGI_MODULE} alone; anywhere under "
        f"{APPLICATION_PACKAGE}/ it would be shared by every importer, every test and every "
        "-n logical worker (baseline B3)."
    )


def test_the_ported_pipeline_container_is_intact(project_root: Path) -> None:
    """Assert the ``node { }`` container this factory ports is still what it was.

    Rule T1 treats every literal in the source artifacts as data rather than as a decision, so
    the construct being ported is asserted rather than described. AAP section 0.4.1 permits
    exactly one change to ``Jenkins``: the two ``mvn clean test`` command strings. The
    container ``[Jenkins:L1]``, its close ``[Jenkins:L17]``, the three stage names
    ``[Jenkins:L2, L6, L14]`` and the checkout URL ``[Jenkins:L3]`` are all preserved
    byte-identically, and if any of them changed, the mapping this whole module rests on would
    no longer hold.

    The stage names are asserted in pipeline order because ordering is itself behaviour: the
    report stage runs after the test stage, which is what makes report generation happen even
    for a failed run (defect D3, preserved).

    Args:
        project_root: The repository root, from ``tests/conftest.py``.
    """
    lines = _pipeline_lines(project_root)

    assert lines, f"{PIPELINE_DEFINITION} is empty"
    assert lines[0] == PIPELINE_CONTAINER_OPEN, (
        f"[{PIPELINE_DEFINITION}:L1] must read {PIPELINE_CONTAINER_OPEN!r} -- the executor "
        f"block this application factory ports -- but reads {lines[0]!r}."
    )
    assert lines[-1] == PIPELINE_CONTAINER_CLOSE, (
        f"The last line of {PIPELINE_DEFINITION} must close the container with "
        f"{PIPELINE_CONTAINER_CLOSE!r}, but reads {lines[-1]!r}."
    )

    positions = [
        next((index for index, line in enumerate(lines) if f"'{stage}'" in line), -1)
        for stage in PIPELINE_STAGE_NAMES
    ]
    absent = [
        stage for stage, index in zip(PIPELINE_STAGE_NAMES, positions, strict=True) if index < 0
    ]
    assert not absent, (
        f"{PIPELINE_DEFINITION} no longer names the stage(s) {absent}. All three stage names "
        f"are preserved verbatim; only the two command strings change (AAP 0.4.1)."
    )
    assert positions == sorted(positions), (
        f"The stages of {PIPELINE_DEFINITION} appear at lines {[index + 1 for index in positions]}, "
        f"which is not the order {list(PIPELINE_STAGE_NAMES)}. Stage ordering is behaviour: the "
        f"report stage runs after the test stage, so a failed run still publishes (defect D3)."
    )

    url = _configured_clone_url(project_root)
    assert url.startswith("https://"), (
        f"[{PIPELINE_DEFINITION}:L{CLONE_STEP_LINE_NUMBER}] must name an https clone URL, but "
        f"names {url!r}."
    )


# =======================================================================================
# The run-time guard.
#
# Building an application needs Flask AND every module the two blueprint packages reach:
# `app/api/__init__.py` imports `app/api/routes.py` from its own last line, which in turn
# imports the service layer. A module missing from a partially provisioned checkout therefore
# surfaces as an ImportError at CALL time, not at import time, and the ordinary `app` fixture
# would report it as an error for every test that asks for one.
#
# This fixture converts that into a single, explicit skip that names what it looked for, and
# is session-scoped on purpose: pytest instantiates higher-scoped fixtures first, so it is
# always set up before the function-scoped `app`, `client` and `config` fixtures from
# tests/conftest.py. The classes below opt in with `usefixtures`, which is what keeps the
# structural assertions above running in exactly the environment where this skips.
# =======================================================================================


@pytest.fixture(scope="session")
def factory() -> Callable[..., Flask]:
    """Return :func:`app.create_app`, or skip the run-time suite with a documented reason.

    The callable is returned rather than an application so a test can build two independent
    instances in one body -- which is the only way to assert the isolation guarantee at the
    heart of baseline B3.

    Returns:
        The application factory, proven to be callable in this environment.
    """
    package_target = f"{FACTORY_FUNCTION_NAME}() from {FACTORY_MODULE}"
    try:
        from app import create_app
    except ImportError as error:  # pragma: no cover - environment-dependent branch
        pytest.skip(
            _SKIP_TEMPLATE.format(
                looked_for=package_target,
                error_type=type(error).__name__,
                error=error,
            )
        )

    # Proven, not assumed. The import above succeeds while a blueprint package is still
    # incomplete, because the blueprint imports live inside the factory body (AAP 0.4.2) and
    # therefore only run when it is called. One throwaway application is the cheapest honest
    # probe, and it is discarded immediately: every test builds its own.
    try:
        create_app(TESTING_CONFIG_NAME)
    except ImportError as error:  # pragma: no cover - environment-dependent branch
        pytest.skip(
            _SKIP_TEMPLATE.format(
                looked_for=(
                    f"a buildable application: {package_target} plus every module "
                    f"{' and '.join(BLUEPRINT_MODULES)} import"
                ),
                error_type=type(error).__name__,
                error=error,
            )
        )

    return create_app


# =======================================================================================
# REQUIREMENT 1 -- baseline B3: every call builds an ISOLATED application.
# =======================================================================================


@pytest.mark.usefixtures("factory")
class TestApplicationIsolation:
    """The factory returns a fresh, independent application on every call.

    AAP section 0.3.3 states why this is structural rather than stylistic: the factory was
    chosen "because ``tests/conftest.py`` needs an isolated application per test, which a
    module-level global cannot provide". AAP section 0.4.2 adds the consequence of getting it
    wrong -- "fixtures would share mutable state across the parallel workers introduced by
    ``-n logical``" -- and that parallelism ports Surefire's method-level parallelism with
    unlimited threads ``[pom.xml:L22-L23]``, so isolation is a parity requirement.
    """

    def test_the_factory_returns_a_flask_application(self, app: Flask) -> None:
        """Assert the object handed back really is a Flask application.

        A Flask instance is itself the WSGI callable, which is why ``wsgi.py`` can hand the
        result straight to gunicorn with no adapter. Both facts are asserted, because a
        subclass or a wrapper that lost ``__call__`` would satisfy the type check alone while
        being unservable.

        Args:
            app: The per-test application from ``tests/conftest.py``.
        """
        import flask

        assert isinstance(app, flask.Flask), (
            f"{FACTORY_FUNCTION_NAME}() returned {type(app)!r}; ``wsgi.py`` annotates its "
            f"module-level name as ``Flask`` and gunicorn loads that object directly."
        )
        assert callable(app), (
            "A Flask application IS the WSGI callable gunicorn invokes, so the returned "
            "object must be callable."
        )

    def test_two_calls_return_distinct_applications(self, factory: Callable[..., Flask]) -> None:
        """Assert two successive calls never hand back the same object.

        This is the assertion a module-level global would fail, and the reason the factory
        exists at all (baseline B3).

        Args:
            factory: The application factory.
        """
        first = factory(TESTING_CONFIG_NAME)
        second = factory(TESTING_CONFIG_NAME)

        assert first is not second, (
            f"{FACTORY_FUNCTION_NAME}() returned the same object twice. Every test and every "
            f"-n logical worker must receive its own application; a shared one makes the "
            f"isolation guarantee imaginary (baseline B3, AAP 0.4.2)."
        )

    def test_applications_do_not_share_mutable_configuration(
        self, factory: Callable[..., Flask]
    ) -> None:
        """Assert a write into one application's configuration is invisible to the next.

        Distinct object identity is necessary but not sufficient: two applications sharing one
        ``config`` mapping would still leak state between tests. The probe key belongs to no
        profile, so its presence anywhere but the application written to is unambiguous.

        Args:
            factory: The application factory.
        """
        first = factory(TESTING_CONFIG_NAME)
        second = factory(TESTING_CONFIG_NAME)

        first.config[ISOLATION_PROBE_KEY] = ISOLATION_PROBE_VALUE

        assert first.config is not second.config, (
            "Two applications share one configuration mapping, so a setting written by one "
            "test is visible to the next."
        )
        assert ISOLATION_PROBE_KEY not in second.config, (
            f"{ISOLATION_PROBE_KEY} leaked into a second application. Configuration is "
            f"resolved per application by app/config.py at the moment of the call, which is "
            f"what lets an environment change between two calls be honoured."
        )
        assert first.config[ISOLATION_PROBE_KEY] == ISOLATION_PROBE_VALUE, (
            "The probe did not survive in the application it was written to, so the isolation "
            "result above proves nothing."
        )

    def test_every_registry_is_per_application(self, factory: Callable[..., Flask]) -> None:
        """Assert the blueprint, error-handler and extension registries are not shared.

        Flask keeps each of these on the application object. Were any of them shared, two
        applications would disagree about their own surface -- and a test that registered a
        handler would silently alter every other test in the process.

        Args:
            factory: The application factory.
        """
        first = factory(TESTING_CONFIG_NAME)
        second = factory(TESTING_CONFIG_NAME)

        registries = {
            "blueprints": (first.blueprints, second.blueprints),
            "error_handler_spec": (first.error_handler_spec, second.error_handler_spec),
            "url_map": (first.url_map, second.url_map),
            "extensions": (first.extensions, second.extensions),
            "after_request_funcs": (first.after_request_funcs, second.after_request_funcs),
        }
        shared = sorted(name for name, (left, right) in registries.items() if left is right)

        assert not shared, (
            f"Two applications share the registr(y/ies) {shared}. Each application must own "
            f"its own, or a registration made for one test mutates every other test in the "
            f"process and every -n logical worker that reuses it."
        )

    def test_the_configuration_selector_selects(self, factory: Callable[..., Flask]) -> None:
        """Assert different profile names produce differently configured applications.

        The factory accepts a selector, so the selector must have an observable effect. AAP
        section 0.3.1 documents the precedence chain that begins with it, and ``app/config.py``
        maps the name to one of three profile classes. Flask's testing mode is the single
        documented difference between the testing and default profiles, which makes it the
        right and only thing to compare here -- the exhaustive per-setting defaults belong to
        ``tests/unit/test_config.py`` (criterion V4).

        Args:
            factory: The application factory.
        """
        testing = factory(TESTING_CONFIG_NAME)
        development = factory(DEVELOPMENT_CONFIG_NAME)

        assert testing.config[CONFIG_NAME_KEY] == TESTING_CONFIG_NAME
        assert development.config[CONFIG_NAME_KEY] == DEVELOPMENT_CONFIG_NAME
        assert testing.config[TESTING_KEY] is True, (
            f"The {TESTING_CONFIG_NAME!r} profile must switch Flask's testing mode on: it is "
            f"what makes a failing view fail a test loudly instead of becoming a 500."
        )
        assert development.config[TESTING_KEY] is False, (
            f"The {DEVELOPMENT_CONFIG_NAME!r} profile must leave Flask's testing mode off, or "
            f"the selector has no observable effect and the parameter is decoration."
        )


# =======================================================================================
# REQUIREMENT 2 -- both blueprints are registered, and the surface is CLOSED.
# =======================================================================================


@pytest.mark.usefixtures("factory")
class TestBlueprintRegistration:
    """``api_bp`` is mounted at ``/api/v1``, ``web_bp`` at the root, and nothing else exists.

    AAP section 0.3.3: "Blueprint Routing -- ``app/api/__init__.py`` defines ``api_bp`` at
    ``/api/v1``; ``app/web/__init__.py`` defines ``web_bp`` at the root." Every rule was
    derived mechanically from a pipeline stage or a report artifact (AAP AMB-6), so the
    surface is closed: AAP section 0.8 is binding -- "No feature may be dropped, and none may
    be added."

    Registration and wiring are what is asserted here. Behavioural coverage of each route
    belongs to ``tests/integration/test_api_routes.py``, and duplicating it would make two
    suites fail for one cause.
    """

    def test_both_blueprints_are_registered(self, app: Flask) -> None:
        """Assert Flask's registry lists the API blueprint and the web blueprint.

        Args:
            app: The per-test application from ``tests/conftest.py``.
        """
        registered = sorted(app.blueprints)
        missing = [name for name in EXPECTED_BLUEPRINTS if name not in app.blueprints]

        assert not missing, (
            f"The factory did not register {missing}. Registered: {registered}. A blueprint "
            f"that is never registered contributes no rule at all, which is the quietest way "
            f"to lose an entire half of the HTTP surface."
        )

    def test_no_unexpected_blueprint_is_registered(self, app: Flask) -> None:
        """Assert no third blueprint has crept in.

        The additive liveness probe is registered on the application object itself, not on a
        blueprint of its own, so exactly two names are expected.

        Args:
            app: The per-test application from ``tests/conftest.py``.
        """
        unexpected = sorted(set(app.blueprints) - set(EXPECTED_BLUEPRINTS))

        assert not unexpected, (
            f"Unexpected blueprints registered: {unexpected}. The surface is closed at the "
            f"rules AAP 0.3.1 enumerates, and {HEALTH_RULE} belongs on the application "
            f"object. AAP 0.8: 'No feature may be dropped, and none may be added.'"
        )

    def test_the_api_blueprint_is_mounted_under_the_versioned_prefix(self, app: Flask) -> None:
        """Assert the API blueprint's rules sit beneath ``/api/v1``.

        Asserted through the registered rules rather than a private attribute, because the
        rules are what a client actually reaches. ``app/api/__init__.py`` declares the prefix
        once and the factory deliberately does not repeat it at registration time: Flask
        concatenates the two, so naming it twice would mount everything at
        ``/api/v1/api/v1/...``. This assertion is what would catch that.

        Args:
            app: The per-test application from ``tests/conftest.py``.
        """
        prefixed = sorted(
            rule for rule in _registered_rules(app) if rule.startswith(f"{API_URL_PREFIX}/")
        )

        assert prefixed, (
            f"No rule begins with {API_URL_PREFIX}/. Either the API blueprint was registered "
            f"without its prefix, or its routes module was never imported -- the latter "
            f"leaves a registered blueprint carrying zero rules, which is silent."
        )
        doubled = sorted(
            rule for rule in prefixed if rule.startswith(f"{API_URL_PREFIX}{API_URL_PREFIX}")
        )
        assert not doubled, (
            f"The prefix is applied twice for {doubled}: the blueprint declares "
            f"{API_URL_PREFIX} itself, so the factory must register it without re-specifying "
            f"url_prefix."
        )

    def test_the_web_blueprint_is_root_mounted(self, app: Flask) -> None:
        """Assert the rendered report surface sits at ``/`` and ``/reports``.

        Those two rules are the HTTP-addressable form of the README's report sections
        ``[README.md:L152-L161]``. A ``url_prefix`` accidentally applied to the web blueprint
        would move both and is exactly what this catches.

        Args:
            app: The per-test application from ``tests/conftest.py``.
        """
        rules = _registered_rules(app)
        missing = [rule for rule in WEB_RULES if rule not in rules]

        assert not missing, (
            f"The root-mounted web blueprint is missing the rule(s) {missing}. Registered "
            f"rules: {sorted(rules)}. The web blueprint carries no url_prefix precisely so "
            f"that its two rules land at {list(WEB_RULES)}."
        )

    def test_the_additive_health_rule_is_registered_on_the_application(self, app: Flask) -> None:
        """Assert ``GET /health`` exists and belongs to the application, not to a blueprint.

        AAP section 0.3.1 marks it "ADDITIVE -- no source equivalent" and gives the only
        justification: "the health endpoint is additive, and it exists because a deployable
        service needs a liveness probe." Its endpoint name carries no ``<blueprint>.`` prefix,
        which is how Flask distinguishes an application-level rule from a blueprint's -- and
        the path is ``/health``, not ``/api/v1/health``, so it could not sit under the API
        blueprint even if ownership allowed it.

        Args:
            app: The per-test application from ``tests/conftest.py``.
        """
        endpoints = {
            rule.endpoint: rule for rule in app.url_map.iter_rules() if rule.rule == HEALTH_RULE
        }

        assert endpoints, (
            f"{HEALTH_RULE} is not registered. Five sibling artifacts assert it -- wsgi.py, "
            f"run.py, gunicorn.conf.py, the Dockerfile health check and docker-compose.yml -- "
            f"so a deployment cannot pass its liveness probe without it."
        )
        assert HEALTH_ENDPOINT in endpoints, (
            f"{HEALTH_RULE} resolves to endpoint(s) {sorted(endpoints)} rather than "
            f"{HEALTH_ENDPOINT!r}. A dotted endpoint name would mean the rule was registered "
            f"on a blueprint, which would make it a third blueprint's rule."
        )
        methods: frozenset[str] = frozenset(endpoints[HEALTH_ENDPOINT].methods or ())
        assert "GET" in methods, (
            f"{HEALTH_RULE} accepts {sorted(methods)} and must accept GET; a liveness probe "
            f"issues nothing else. Werkzeug adds HEAD and OPTIONS itself, which is correct."
        )

    def test_every_documented_api_rule_is_registered(self, app: Flask) -> None:
        """Assert all eight ``/api/v1`` rules of AAP section 0.3.1 are present.

        A presence assertion only. What each route *does* -- the three ported stages, run
        status and the three report artifacts -- is owned by
        ``tests/integration/test_api_routes.py`` and by criterion V11.

        Args:
            app: The per-test application from ``tests/conftest.py``.
        """
        rules = _registered_rules(app)
        missing = [rule for rule in API_RULES if rule not in rules]

        assert not missing, (
            f"The API blueprint is missing the rule(s) {missing}. Registered rules: "
            f"{sorted(rules)}. Every one traces to a pipeline stage or a report artifact "
            f"(AAP AMB-6), so a missing rule drops a ported feature."
        )

    def test_the_http_surface_is_closed(self, app: Flask) -> None:
        """Assert the application registers no rule beyond the documented surface.

        AAP AMB-6 derived the surface mechanically from the three pipeline stages plus the
        four report artifacts, and AAP section 0.8 forbids adding a feature, so an extra rule
        would invent capability the source system never had. Flask's own
        ``/static/<path:filename>`` is expected -- it is how ``app/static/css/main.css``
        reaches the rendered report surface.

        Converter prefixes are normalised before comparison, because ``<string:run_id>`` and
        ``<run_id>`` route identically while rendering as different strings.

        Args:
            app: The per-test application from ``tests/conftest.py``.
        """
        unexpected = sorted(_registered_rules(app) - DOCUMENTED_RULES)

        assert not unexpected, (
            f"Undocumented rule(s) registered: {unexpected}. The surface is closed at "
            f"{sorted(DOCUMENTED_RULES)}. AAP 0.8: 'No feature may be dropped, and none may "
            f"be added.'"
        )


# =======================================================================================
# REQUIREMENT 3 -- baseline B8: the error handlers are registered at APPLICATION scope.
#
# This is the single most easily missed guarantee in the file, and it came out of research
# rather than taste. AAP 0.3.2, verbatim: "Blueprint-level 404 and 405 handlers are not
# invoked for invalid URLs, because a blueprint does not own a URL space. This is why
# app/errors.py registers handlers at application level ... Had this been overlooked, unknown
# routes would have produced Flask's default HTML error page instead of the intended
# structured response."
#
# It is therefore asserted twice: structurally, through Flask's own registry, and
# behaviourally, through real requests. The structural half alone could pass while the
# handlers never fired; the behavioural half alone would not stop a future refactor from
# moving them onto a blueprint and happening to keep working for the paths it tests.
# =======================================================================================


@pytest.mark.usefixtures("factory")
class TestErrorHandlerScope:
    """404, 405 and 500 are handled by the application, and the handlers demonstrably fire."""

    def test_the_handlers_are_registered_at_application_scope(self, app: Flask) -> None:
        """Assert all three statuses appear under the application key of the handler registry.

        Flask keys ``error_handler_spec`` by blueprint name and files an application-scoped
        handler under ``None``. A handler filed under a blueprint name would never be consulted
        for a URL that matched no rule, because no blueprint owns that URL.

        Args:
            app: The per-test application from ``tests/conftest.py``.
        """
        spec = app.error_handler_spec

        assert APPLICATION_HANDLER_SCOPE in spec, (
            f"No application-scoped error handler is registered at all: "
            f"error_handler_spec keys are {sorted(str(key) for key in spec)}. app/errors.py's "
            f"register_error_handlers(app) must be called with the application object."
        )

        application_scope = spec[APPLICATION_HANDLER_SCOPE]
        missing = [status for status in HANDLED_STATUSES if not application_scope.get(status)]

        assert not missing, (
            f"Status(es) {missing} have no application-scoped handler; registered statuses are "
            f"{sorted(status for status in application_scope if status is not None)}. Baseline "
            f"B8 requires all of {list(HANDLED_STATUSES)} at application level."
        )

    def test_no_blueprint_shadows_the_application_handlers(self, app: Flask) -> None:
        """Assert no blueprint carries a 404 or 405 handler of its own.

        A blueprint-scoped handler for either status is not merely redundant, it is misleading:
        it looks like coverage while never firing for the unmatched URLs that actually produce
        those statuses. Per-blueprint handlers are reserved for errors raised *inside* a
        blueprint view, which is a different situation entirely.

        Args:
            app: The per-test application from ``tests/conftest.py``.
        """
        url_level_statuses = (404, 405)
        misplaced = [
            f"blueprint {scope!r} handles {status}"
            for scope, handlers in app.error_handler_spec.items()
            if scope is not APPLICATION_HANDLER_SCOPE
            for status in url_level_statuses
            if handlers.get(status)
        ]

        assert not misplaced, (
            "Blueprint-scoped URL-level error handler(s) found: "
            + "; ".join(misplaced)
            + ". AAP 0.3.2: such a handler is never invoked for an invalid URL, because a "
            "blueprint does not own a URL space, so unknown routes would fall through to "
            "Flask's default HTML error page."
        )

    def test_an_unmatched_url_is_answered_by_the_rendered_page(self, client: FlaskClient) -> None:
        """Assert an unknown non-API URL returns a 404 rendered by this project's templates.

        The decisive discriminator is not the copy but the layout: a page extending
        ``app/templates/base.html`` links ``app/static/css/main.css``, and Flask's built-in
        error page never does. The framework page's own title is asserted absent as well, so a
        fall-through to the default cannot pass unnoticed.

        Args:
            client: The test client bound to this test's application.
        """
        response = client.get(UNMATCHED_PATH)

        assert response.status_code == 404, (
            f"GET {UNMATCHED_PATH} returned {response.status_code}; a path matching no rule "
            f"must produce 404."
        )
        assert response.mimetype == HTML_MIMETYPE, (
            f"A non-API 404 must be a rendered page, but the content type was "
            f"{response.mimetype!r} (baseline B8)."
        )
        assert RENDERED_LAYOUT_MARKER in response.data, (
            f"The 404 body does not reference {RENDERED_LAYOUT_MARKER.decode()}, so it was not "
            f"rendered from this project's templates. That is the symptom AAP 0.3.2 warns "
            f"about: the application-level handler did not fire."
        )
        assert FRAMEWORK_NOT_FOUND_MARKER not in response.data, (
            f"The 404 body carries {FRAMEWORK_NOT_FOUND_MARKER.decode()!r}, which is Flask's "
            f"own default page -- exactly what a blueprint-scoped handler would leave in place."
        )

    def test_an_unmatched_api_url_is_answered_with_the_structured_envelope(
        self, client: FlaskClient
    ) -> None:
        """Assert an unknown ``/api/v1`` URL returns the JSON error envelope.

        Baseline B8 asks for "structured responses for the API blueprint and rendered pages
        for the web blueprint", so one application-scoped handler has to serve two shapes and
        choose between them by path. Both halves of that choice are asserted -- this test and
        the one above are a pair.

        Args:
            client: The test client bound to this test's application.
        """
        response = client.get(f"{API_URL_PREFIX}{UNMATCHED_PATH}")

        assert response.status_code == 404
        assert response.mimetype == JSON_MIMETYPE, (
            f"An error beneath {API_URL_PREFIX} must be JSON whatever the client asked for, "
            f"but the content type was {response.mimetype!r}."
        )

        payload = response.get_json()
        assert isinstance(payload, dict), f"Expected a JSON object, got {type(payload)!r}"
        assert ERROR_ENVELOPE_KEYS <= set(payload), (
            f"The error envelope is missing {sorted(ERROR_ENVELOPE_KEYS - set(payload))}; a "
            f"client must parse one error shape for every failure mode."
        )
        assert payload["error"] == NOT_FOUND_ERROR_CODE
        assert payload["status"] == 404

    def test_a_disallowed_method_is_answered_with_405(self, client: FlaskClient) -> None:
        """Assert a wrong method on an existing rule returns 405 with an ``Allow`` header.

        405 is the other status a blueprint-scoped handler would fail to serve, so it is
        exercised for the same reason as the 404 above. The header is asserted because
        building a response by hand drops the one Werkzeug's exception would have set, and
        RFC 9110 requires it.

        Args:
            client: The test client bound to this test's application.
        """
        response = client.get(POST_ONLY_API_PATH)

        assert response.status_code == 405, (
            f"GET {POST_ONLY_API_PATH} returned {response.status_code}. That rule ports the "
            f"pipeline's checkout stage and accepts POST only, so GET must be rejected with "
            f"405 rather than 404 -- the path exists."
        )
        assert response.mimetype == JSON_MIMETYPE

        allowed = response.headers.get(ALLOW_HEADER)
        assert allowed is not None, (
            f"The 405 response carries no {ALLOW_HEADER} header. RFC 9110 requires it, and it "
            f"is the only way a client learns which method the rule does accept."
        )
        assert "POST" in allowed, (
            f"{ALLOW_HEADER}: {allowed!r} does not name POST, which is the method "
            f"{POST_ONLY_API_PATH} exists to serve."
        )

        payload = response.get_json()
        assert isinstance(payload, dict), f"Expected a JSON object, got {type(payload)!r}"
        assert payload["error"] == METHOD_NOT_ALLOWED_ERROR_CODE
        assert payload["status"] == 405


# =======================================================================================
# REQUIREMENT 4 -- what the factory must wire, and what it must delegate.
# =======================================================================================


@pytest.mark.usefixtures("factory")
class TestFactoryWiring:
    """Configuration, structured logging, extension binding and filesystem tolerance."""

    def test_configuration_is_applied(
        self,
        factory: Callable[..., Flask],
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Assert the built application carries resolved configuration, not Flask's defaults.

        One spot-check, deliberately: the clone URL of the checkout stage
        ``[Jenkins:L3]``. The exhaustive per-setting defaults are criterion V4 and belong to
        ``tests/unit/test_config.py``; repeating them here would make two suites fail for one
        cause.

        The expected value is read back out of ``Jenkins`` rather than restated, so the
        assertion cannot drift from the pipeline it claims parity with (baseline B11, Rule T1).
        Because the configuration chain lets an environment variable, ``.env`` or
        ``configuration.properties`` legitimately outrank the hard-coded default, the
        environment override is removed and the two optional files are checked for -- a
        checkout that carries either is reported as a skip rather than as a failure, since
        overriding is the documented behaviour rather than a defect.

        Args:
            factory: The application factory.
            project_root: The repository root, from ``tests/conftest.py``.
            monkeypatch: Used to remove an environment override for this test only.
        """
        # Reached by module path, so the `app` package resolves through `sys.modules` and is
        # unaffected by the fixture of the same name used elsewhere in this class.
        from app.config import DEFAULT_CONFIGURATION_PROPERTIES_PATH, dotenv_path

        for path in (dotenv_path(), DEFAULT_CONFIGURATION_PROPERTIES_PATH):
            if path.is_file():
                pytest.skip(
                    f"{path.name} is present in this checkout and legitimately outranks the "
                    f"hard-coded default for {CLONE_URL_KEY}, so the resolved value is not "
                    f"comparable with [{PIPELINE_DEFINITION}:L{CLONE_STEP_LINE_NUMBER}]. "
                    f"Looked for an application whose {CLONE_URL_KEY} equals the pipeline's "
                    f"checkout URL with no override in force."
                )

        monkeypatch.delenv(CLONE_URL_KEY, raising=False)
        built = factory(TESTING_CONFIG_NAME)

        assert built.config[CONFIG_NAME_KEY] == TESTING_CONFIG_NAME, (
            f"The application does not record the profile it was built with; "
            f"{CONFIG_NAME_KEY} is {built.config.get(CONFIG_NAME_KEY)!r}."
        )
        assert built.config[CLONE_URL_KEY] == _configured_clone_url(project_root), (
            f"{CLONE_URL_KEY} resolved to {built.config.get(CLONE_URL_KEY)!r} rather than the "
            f"URL the checkout stage names at [{PIPELINE_DEFINITION}:"
            f"L{CLONE_STEP_LINE_NUMBER}]. Rule T1: configuration values are data carried "
            f"across unchanged, not decisions to revisit."
        )
        assert TARGET_DIR_KEY in built.config, (
            f"{TARGET_DIR_KEY} is absent, so the factory received Flask's bare defaults rather "
            f"than the configuration app/config.py resolved."
        )

    def test_structured_logging_is_initialised_by_the_factory(
        self, factory: Callable[..., Flask]
    ) -> None:
        """Assert the factory installs logging handlers while building an application.

        Asserted as a handler *delta* across one factory call, observed through the standard
        library alone: whatever handlers the root logger carried beforehand, the call must add
        at least one of its own. That phrasing is deliberate on three counts.

        * It is evidence about *this* call rather than about a previous test in the same
          process, because it compares before against after instead of reading a global.
        * It cannot be satisfied by pytest's capture handler, gunicorn's, or a caller's, since
          those are all present in the *before* snapshot and therefore excluded from the delta.
        * It needs no reset and no restore, so it cannot silence the diagnostics of the tests
          that follow -- the process is left with logging correctly configured, which is
          precisely the state the factory is supposed to leave behind.

        Reading the effect through :mod:`logging` rather than importing
        ``app/logging_config.py`` also keeps this module inside its declared dependency set:
        the delegation itself is asserted structurally by
        :func:`test_factory_wires_configuration_logging_extensions_and_handlers`, which reads
        ``app/__init__.py``. No log destination is asserted here: the path is
        ``app/logging_config.py``'s concern, it deliberately sits outside the ephemeral
        artifact root, and it ends ``.log`` so ``[.gitignore:L6]`` covers it.

        Args:
            factory: The application factory.
        """
        root = logging.getLogger()
        before = list(root.handlers)

        factory(TESTING_CONFIG_NAME)

        installed = [handler for handler in root.handlers if handler not in before]
        assert installed, (
            "The factory built an application without installing any logging handler. "
            "AAP 0.4.1 lists logging among the four things it wires, and every diagnostic "
            "emitted afterwards would otherwise escape unformatted and unredacted. Handlers "
            f"on the root logger were unchanged at {len(root.handlers)}."
        )

    def test_extension_singletons_are_bound_by_the_factory(
        self, factory: Callable[..., Flask]
    ) -> None:
        """Assert extensions are bound per application, not to one instance at import time.

        AAP section 0.3.2 records this as the researched reason the factory pattern was chosen:
        Flask "recommends the application-factory function so extensions are not bound to one
        application instance at import time". ``app/extensions.py`` therefore declares its
        singleton unbound, and the factory binds it once per application.

        The proof is a bare application created *after* the extensions module has been
        imported: it carries no hook at all, which is only true if importing the module binds
        nothing. Each factory-built application then carries its own.

        Args:
            factory: The application factory.
        """
        import flask

        from app import extensions

        assert hasattr(extensions, "cors"), (
            "app/extensions.py publishes no 'cors' singleton, so nothing observable is left "
            "to assert about extension binding."
        )

        bare = flask.Flask(__name__)
        assert not bare.after_request_funcs, (
            f"A bare Flask application already carries the response hook(s) "
            f"{sorted(str(scope) for scope in bare.after_request_funcs)} merely because "
            f"app/extensions.py was imported. Importing the module must bind nothing."
        )

        first = factory(TESTING_CONFIG_NAME)
        second = factory(TESTING_CONFIG_NAME)

        for built in (first, second):
            assert built.after_request_funcs.get(APPLICATION_HANDLER_SCOPE), (
                "A factory-built application carries no application-wide response hook, so "
                "the extension singleton was never bound to it inside the factory."
            )
        assert first.after_request_funcs is not second.after_request_funcs, (
            "Two applications share one response-hook registry, so binding an extension for "
            "one alters the other."
        )

    def test_a_hostile_artifact_filesystem_does_not_prevent_start_up(
        self,
        factory: Callable[..., Flask],
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Assert the factory logs and continues when the artifact tree cannot be created.

        The factory runs while ``wsgi.py`` is being imported, so raising here would make
        ``import wsgi`` fail outright on a read-only or otherwise restricted filesystem -- a
        container that cannot start rather than one that starts without artifact directories.
        The requirement is to log and continue.

        The hostile filesystem is arranged by making the artifact root's parent a regular
        *file*, so ``mkdir`` raises ``NotADirectoryError``. That is chosen over removing write
        permission because it restricts the superuser too, and CI commonly runs as root -- a
        ``chmod``-based version of this test would silently pass there while asserting nothing.

        Args:
            factory: The application factory.
            tmp_path: A per-test temporary directory, unique to each ``-n logical`` worker.
            caplog: Captures the warning the degraded path must emit.
        """
        import flask

        blocked = tmp_path / "blocked"
        blocked.write_text(
            "a regular file, so nothing can be created beneath it\n", encoding="utf-8"
        )
        hostile_root = blocked / ARTIFACT_ROOT_NAME

        with caplog.at_level(logging.WARNING):
            built = factory(TESTING_CONFIG_NAME, **{TARGET_DIR_KEY: str(hostile_root)})

        assert isinstance(built, flask.Flask), (
            f"{FACTORY_FUNCTION_NAME}() did not return an application when the artifact tree "
            f"could not be created. Importing wsgi.py must still succeed on a restricted "
            f"filesystem."
        )
        assert not hostile_root.exists(), (
            "The hostile artifact root was created after all, so this test proved nothing "
            "about the degraded path."
        )

        warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
        assert warnings, (
            "The factory absorbed a filesystem failure silently. Degrading without a warning "
            "turns a missing report artifact into an unexplained one; baseline B9 requires the "
            "diagnostic to reach the structured log."
        )
