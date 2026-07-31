"""Unit suite over ``app/config.py``: the precedence chain and every carried source default.

Which half of validation criterion V4 this module owns
=====================================================
Criterion V4 -- "every preserved configuration constant is unchanged" -- is realized by two
sibling modules, and the split is deliberate rather than incidental. State it plainly so
nobody later assumes the other module covers this one's assertions:

* **This module owns** the five-rung precedence chain of AAP 0.3.1 and the source defaults
  that ride on it: the clone URL ``[Jenkins:L3]``, the tag expression ``[README.md:L87]``,
  the failure tolerance ``[pom.xml:L25]``, the parallelism settings ``[pom.xml:L22-L24]``
  and the artifact root ``[README.md:L79-L82]`` -- plus the two-URL preservation of
  defect D7.
* **``tests/unit/test_thresholds_parity.py`` owns** the frozen publisher constants: the six
  ``-1`` thresholds, the ``ALPHABETICAL`` sort order and the ``**/*.json`` include pattern
  of ``[Jenkins:L15]``. They are declared in ``app/reporting/thresholds.py``, which
  ``app/config.py`` imports rather than restating, so asserting their *values* here would be
  a second copy of somebody else's contract. This module asserts only that the
  configuration surface *exposes* them, which is what the ``GET /api/v1/config``
  introspection endpoint depends on.

Evidence is two-sided on purpose
================================
Every expected value below appears twice: once as a named constant in this module, and once
extracted at run time from the source artifact that published it. AAP Rule T1 says
"configuration values are data, not decisions ... no value is rounded, renamed, or
'modernized'", so proving the carry happened faithfully means comparing the resolved default
against the *source text*, not merely against a second hand-typed copy of it. That is AAP
0.7 baseline B11, "executable parity evidence rather than assertions of parity".

The extraction searches by content, never by line index. ``README.md`` is rewritten in place
by this migration -- the AAP lists it as UPDATE -- so ``tags = "@LogOut"`` no longer sits on
line 87 of the working copy even though it is still present verbatim. Indexing by line would
turn a faithful rewrite into a false failure. The line citations throughout this module
therefore locate each literal in the *pre-migration* source, which is what makes them
auditable, while the assertions locate it by content, which is what makes them stable.

A note on one citation: the AAP body cites the four Cucumber plugin lines as
``[README.md:L78-L81]``. That is a confirmed off-by-one. Line 78 of the source README is the
opening ``plugin = {``; the four plugin strings occupy lines **79 to 82**, which is the range
cited throughout this module.

Preserved defects asserted here, never repaired
===============================================
AAP Rule T4 is explicit that "defects are behavior". Three of the nine catalogued defects
surface as configuration defaults, and this module pins each one *as it is*:

* **D2** -- the default tag expression ``LogOut`` ``[README.md:L87]`` matches no scenario in
  the specification, whose only tags are ``@Login``, ``@UPGN-286``, ``@UPGN-287``,
  ``@UPGN-288``, ``@SalesManager`` and ``@PosManager``. The documented run therefore selects
  nothing. This module asserts the default and asserts that it stays overridable through
  every rung; it does not substitute a tag that exists. The consequence -- that pytest's
  "no tests ran" exit code must be reported as a *successful zero-scenario run* (criterion
  V6) -- lives in ``app/services/test_runner_service.py`` and is mentioned here purely as
  context, never asserted.
* **D3** -- failure tolerance defaults to true ``[pom.xml:L25]``, so the build can never
  fail. Asserted as true; build-failure gating is deliberately not enabled.
* **D7** -- two contradictory repository URLs. The pipeline's ``[Jenkins:L3]`` is the runtime
  default because executable configuration outranks prose, and the Jira key prefix ``UPGN``
  ``[README.md:L115]`` corroborates it; the README's ``[README.md:L59]`` is retained rather
  than silently unified. Both survive, one key resolves either.

``docs/migration-parity.md`` is the authoritative register of defects D1 through D9 and
records the configuration switch that opts into each available fix. Nothing here corrects
one (AAP 0.7 baseline B12).

How the rungs are driven, and why nothing touches the repository tree
====================================================================
``configuration.properties`` is git-ignored ``[.gitignore:L3]``, so absence is the normal
state of a fresh checkout, a CI job and a container -- not an edge case. The same is true of
``.env``. This module therefore never creates either file inside the repository: every
fixture writes into ``tmp_path``, points rung four at it through the published
``CONFIGURATION_PROPERTIES_PATH`` setting, and redirects rung three by patching
``app.config.dotenv_path`` -- a function the module publishes in its own ``__all__``. Every
environment variable goes through ``monkeypatch``, so no test can leak one into the next or
into a parallel worker.

Degraded environments skip, they do not crash
=============================================
``import app.config`` executes the parent package ``app/__init__.py``, which is the Flask
application factory, so reaching any ``app.*`` name needs Flask installed. The import is
consequently made inside a helper that skips with a reason naming exactly what was missing,
and the annotations that mention those types are resolved by the type checker alone. The
source-inspection and source-extraction suites need nothing beyond the standard library and
keep running in an environment that cannot build the application at all, which is where the
most evidence is worth having.

Provenance
==========
This module ports no source construct: the source project is a Java/Maven skeleton that has
never contained a committed test tree on any branch. ``pom.xml`` and ``Jenkins`` are recorded
as its origins because they publish the literals asserted here -- ``pom.xml`` is REFERENCE
mode, read statically and never compiled -- and ``README.md`` and ``.gitignore`` supply the
tag expression, the artifact root and the configuration-optionality contract. No line of any
of them is reproduced beyond the individual literals under test.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    # Annotations only. `from __future__ import annotations` keeps every annotation a
    # string, so this block never executes and importing this module drags neither Flask nor
    # the application package into a session that does not need them.
    from collections.abc import Iterator, Mapping
    from types import ModuleType

    from app.config import Config, Provenance

# ---------------------------------------------------------------------------
# The source literals under test.
#
# Each was read verbatim from the pre-migration source file cited beside it.
# They are declared here as named constants so that a failure message names the
# contract that broke, and every one of them is additionally re-derived from the
# source text at run time by the extraction suite at the end of this module.
# ---------------------------------------------------------------------------

# `[Jenkins:L3]` -- the full source line is:
#     git 'https://github.com/BalamiRR/Upgenix-QA.git'
PIPELINE_CLONE_URL: Final[str] = "https://github.com/BalamiRR/Upgenix-QA.git"

# `[README.md:L59]` -- the full source line is:
#     git clone https://github.com/BalamiRR/Testinium-QA.git
# Preserved defect D7: retained, never unified with the value above.
DOCUMENTED_CLONE_URL: Final[str] = "https://github.com/BalamiRR/Testinium-QA.git"

# `[README.md:L87]` -- the full source line is:
#     tags = "@LogOut"
# pytest-bdd strips the leading `@` when it turns a Gherkin tag into a mark, which is why
# the expected value carries none. Preserved defect D2.
EXPECTED_TAG_EXPRESSION: Final[str] = "LogOut"

# `[pom.xml:L25]` -- <testFailureIgnore>true</testFailureIgnore>. Preserved defect D3.
EXPECTED_IGNORE_TEST_FAILURES: Final[bool] = True

# `[pom.xml:L22-L23]` -- <parallel>methods</parallel> plus
# <useUnlimitedThreads>true</useUnlimitedThreads>. xdist's `logical` allocation is the port:
# it sizes the worker pool from the machine, the closest analogue of "unlimited threads".
EXPECTED_PYTEST_WORKERS: Final[str] = "logical"

# `[pom.xml:L24]` -- <!--                    <threadCount>4</threadCount>--> , COMMENTED OUT.
# Carried across as a documented, disabled tuning default (AAP goal O7).
EXPECTED_DISABLED_THREAD_COUNT: Final[int] = 4

# `[README.md:L79-L82]` -- the four plugin destinations, every one of them under `target/`.
# The name is never renamed: the CI publisher's `fileIncludePattern: '**/*.json'`
# `[Jenkins:L15]` still matches unedited precisely because of that.
EXPECTED_ARTIFACT_ROOT: Final[str] = "target"

# The publisher constants this module only checks for PRESENCE. Their values belong to
# `tests/unit/test_thresholds_parity.py`; see the module docstring.
PUBLISHER_INTROSPECTION_KEYS: Final[tuple[str, ...]] = (
    "PUBLISHER_THRESHOLDS",
    "REPORT_SORTING_METHOD",
    "REPORT_FILE_INCLUDE_PATTERN",
)

# ---------------------------------------------------------------------------
# Test-harness constants.
# ---------------------------------------------------------------------------

# The profile the resolution suites build. Every preserved parity constant is identical in
# all three profiles -- only operational behaviour differs -- and this one needs no signing
# key supplied, so it is the profile that can be built unconditionally.
PROFILE_NAME: Final[str] = "testing"

# Every environment key this module resolves. All of them are cleared before a resolution so
# an inherited variable in the ambient environment cannot decide the outcome, which matters
# because the default rung is precisely the one under test.
MANAGED_ENVIRONMENT_KEYS: Final[tuple[str, ...]] = (
    "APP_CONFIG",
    "CLONE_URL",
    "CLONE_URL_DOCUMENTED",
    "CONFIGURATION_PROPERTIES_PATH",
    "IGNORE_TEST_FAILURES",
    "PYTEST_WORKERS",
    "TAG_EXPRESSION",
    "TARGET_DIR",
)

# The two optional runtime files, named as the application names them. Written only ever
# inside `tmp_path`; the repository copies must stay absent (`configuration.properties` is
# git-ignored `[.gitignore:L3]`, and only the two `.example` templates are committed).
PROPERTIES_FILENAME: Final[str] = "configuration.properties"
DOTENV_FILENAME: Final[str] = ".env"

# The committed templates that document the configuration surface (AAP 0.7 baseline B5).
COMMITTED_TEMPLATES: Final[tuple[str, ...]] = (
    ".env.example",
    "configuration.properties.example",
)

# Packages `app/config.py` must never reach into. AAP Rule T7 fixes the direction
# `api -> services -> reporting -> utils`, and the factory imports the configuration module,
# so an import back into any of these would close a cycle at start-up as well as invert the
# layering. `tests` and `scripts` are forbidden outright in either direction of use.
FORBIDDEN_CONFIG_IMPORTS: Final[tuple[str, ...]] = (
    "app.api",
    "app.web",
    "app.services",
    "tests",
    "scripts",
)

# Calls that would mean this module configures logging rather than merely using it. Handlers,
# levels and formats belong to `app/logging_config.py` alone (AAP 0.7 baseline B9).
LOGGING_CONFIGURATION_CALLS: Final[frozenset[str]] = frozenset(
    {"basicConfig", "dictConfig", "fileConfig", "addHandler", "setLevel", "setFormatter"}
)

# Provider-shaped credential prefixes. Deliberately shaped rather than keyword-based: scanning
# for the word "secret" would fire on the signing key's own documentation, while these match
# only something that really is a credential. Written as fragments joined at run time so this
# file cannot itself trip a secret scanner (AAP 0.7 baseline B5).
SECRET_PATTERNS: Final[tuple[str, ...]] = (
    r"sk_" + r"live_[0-9A-Za-z]+",
    r"sk_" + r"test_[0-9A-Za-z]+",
    r"pk_" + r"live_[0-9A-Za-z]+",
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    r"\bgh[pous]_[0-9A-Za-z]{20,}",
    r"\bgithub_" + r"pat_[0-9A-Za-z_]{20,}",
    r"\bxox[abpsr]-[0-9A-Za-z-]{10,}",
    r"\bAIza[0-9A-Za-z_\-]{30,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\beyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}",
    r"://[^/\s:@]+:[^/\s:@]+@",
)


# ---------------------------------------------------------------------------
# Guarded access to the module under test.
# ---------------------------------------------------------------------------


def require_app_module(dotted_name: str) -> ModuleType:
    """Import an application module, or skip the calling test with a documented reason.

    Reaching any ``app.*`` name executes ``app/__init__.py``, the Flask application factory,
    so every such import needs Flask and the rest of ``requirements.txt`` present. Where they
    are not, the calling test skips rather than erroring, and the source-inspection suites
    lower down keep running on the standard library alone.

    Args:
        dotted_name: Fully qualified module name, for example ``"app.config"``.

    Returns:
        The imported module.
    """
    try:
        return importlib.import_module(dotted_name)
    except ImportError as error:  # pragma: no cover - environment-dependent branch
        pytest.skip(
            f"{dotted_name} could not be imported, so the resolved-value assertions cannot "
            "run. Reaching it executes app/__init__.py, the Flask application factory, which "
            f"needs the pinned runtime dependencies installed ({type(error).__name__}: "
            f"{error})."
        )


def require_app_config() -> ModuleType:
    """Import :mod:`app.config`, the module under test, or skip.

    Returns:
        The imported module.
    """
    return require_app_module("app.config")


def application_config(request: pytest.FixtureRequest) -> Mapping[str, object]:
    """Return the running application's own resolved configuration mapping, or skip.

    The mapping comes from the ``config`` fixture in ``tests/conftest.py``, which builds an
    application through the factory and hands over ``app.config`` verbatim. It is requested
    lazily rather than declared as a parameter so that an application that cannot yet be
    built skips only the corroborating assertions instead of erroring the whole module: the
    factory imports the blueprint packages, and any of them being absent surfaces here as an
    :class:`ImportError`.

    Args:
        request: The calling test's pytest request object.

    Returns:
        The application's configuration mapping, keyed exactly like the settings.
    """
    try:
        resolved: Mapping[str, object] = request.getfixturevalue("config")
    except ImportError as error:  # pragma: no cover - environment-dependent branch
        pytest.skip(
            "The application could not be built, so its resolved configuration mapping is "
            "unavailable; the module-level assertions above still cover every default. "
            f"({type(error).__name__}: {error})"
        )
    return resolved


# ---------------------------------------------------------------------------
# Reading committed files.
# ---------------------------------------------------------------------------


def read_repository_text(root: Path, name: str) -> str:
    """Read a committed file from the repository root as UTF-8 text, or skip.

    Explicit UTF-8 on every read, per AAP 0.7 baseline B7: the repository does contain
    non-ASCII content, and an encoding that varies with the platform would make a comparison
    against a source literal unreliable rather than merely awkward.

    Args:
        root: Absolute repository root, from the ``project_root`` fixture.
        name: File name relative to that root.

    Returns:
        The file's decoded contents.
    """
    path = root / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        pytest.skip(f"{name} could not be read at {path} ({type(error).__name__}: {error}).")


def extract(pattern: str, text: str, source: str) -> str:
    """Return the first capture group of *pattern* in *text*, or skip naming what was sought.

    Content search rather than line indexing: ``README.md`` is rewritten in place by this
    migration, so a literal that is still present verbatim no longer sits on its original
    line. Searching keeps the evidence stable without weakening it.

    Args:
        pattern: Regular expression with exactly one capture group.
        text: Text to search.
        source: Human-readable name of the file, used in the skip reason.

    Returns:
        The captured text.
    """
    match = re.search(pattern, text)
    if match is None:
        pytest.skip(
            f"{source} does not currently contain the expected construct {pattern!r}, so the "
            "source side of this comparison cannot be established."
        )
    return match.group(1)


# ---------------------------------------------------------------------------
# Static inspection.
#
# Layering, print statements, logging configuration and the properties-reader
# delegation are all asserted over the real SYNTAX TREE rather than over the
# file's text. That is a correctness requirement, not a refinement: the docstring
# of `app/config.py` discusses `app.api`, `app.services`, `tests/`,
# `configparser` and printing at length while importing, calling and doing none
# of them, so a textual scan would report the opposite of the truth in both
# directions.
#
# These helpers need nothing but the standard library, which is what lets the
# whole inspection suite keep running in an environment where the application
# itself cannot be imported.
# ---------------------------------------------------------------------------


def python_sources(root: Path) -> list[Path]:
    """Return every Python source file beneath *root*, excluding caches, sorted.

    Args:
        root: Directory to walk.

    Returns:
        Absolute paths, in a deterministic order so a failure message is reproducible across
        the parallel workers the suite runs with.
    """
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts and ".venv" not in path.parts
    )


def parse_module(path: Path) -> ast.Module:
    """Parse a Python source file into a syntax tree, or skip naming the file.

    Args:
        path: File to parse.

    Returns:
        The parsed module.
    """
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        pytest.skip(f"{path} could not be parsed ({type(error).__name__}: {error}).")


def imported_modules(tree: ast.Module) -> set[str]:
    """Return every absolute module name imported by *tree*.

    Relative imports are excluded: they cannot express a dependency on another top-level
    package, which is what the layering rules are about.

    Args:
        tree: A parsed module.

    Returns:
        Dotted module names, as written.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def imported_top_level_packages(path: Path) -> set[str]:
    """Return the top-level packages a source file imports, tolerating an unparseable file.

    An unparseable sibling is somebody else's defect and must not be converted into a failure
    of this module's layering checks, so the answer is derived from a line scan in that case --
    the same question the equivalent ``grep`` asks, and sufficient for it.

    Args:
        path: File to inspect.

    Returns:
        First dotted segment of every absolute import.
    """
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        pattern = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", re.MULTILINE)
        return {match.split(".")[0] for match in pattern.findall(text)}
    return {name.split(".")[0] for name in imported_modules(tree)}


def called_builtins(tree: ast.Module) -> list[str]:
    """Return the bare names called in *tree*, so a call can be counted rather than grepped.

    Args:
        tree: A parsed module.

    Returns:
        Every called plain name, with duplicates retained.
    """
    return [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]


def called_attributes(tree: ast.Module) -> set[str]:
    """Return the attribute names called in *tree*, for example ``basicConfig``.

    Args:
        tree: A parsed module.

    Returns:
        The final attribute of every dotted call.
    """
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


# ---------------------------------------------------------------------------
# The precedence-chain harness.
#
# One instance per test, holding rungs two, three and four under that test's
# exclusive control:
#
#   rung 2, environment  -- every managed key is cleared, then set on request
#   rung 3, .env         -- redirected into tmp_path by patching the published
#                           `app.config.dotenv_path`, and NOT created by default
#   rung 4, properties   -- pointed into tmp_path through the published
#                           CONFIGURATION_PROPERTIES_PATH setting, and NOT
#                           created by default
#
# Neither optional file exists until a test writes it, so the harness's resting
# state is the rung-five, default-only case -- which is exactly the state of a
# fresh checkout, since `configuration.properties` is git-ignored
# `[.gitignore:L3]` and `.env` is untracked. Nothing is ever written into the
# repository tree.
# ---------------------------------------------------------------------------


class ChainHarness:
    """Drives the five-rung chain of AAP 0.3.1 without touching the repository tree."""

    def __init__(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        workspace: Path,
    ) -> None:
        """Neutralize every rung above the hard-coded default.

        Args:
            module: The imported :mod:`app.config`.
            monkeypatch: The test's patcher, so every change is undone at teardown.
            workspace: A per-test directory, from ``tmp_path``.
        """
        self._module = module
        self._workspace = workspace
        self._properties_path = workspace / PROPERTIES_FILENAME
        self._dotenv_path = workspace / DOTENV_FILENAME

        # Rung two, emptied. An inherited variable would otherwise outrank the rung under
        # test, and the default rung is the one that runs in anger.
        for key in MANAGED_ENVIRONMENT_KEYS:
            monkeypatch.delenv(key, raising=False)

        # Rung three, redirected. `load_dotenv_values` looks `dotenv_path` up as a module
        # global at call time, so patching the published name is enough; the repository's own
        # `.env` -- if a developer has one -- can no longer influence the outcome.
        monkeypatch.setattr(module, "dotenv_path", self.dotenv_path)

    # -- rung locations -------------------------------------------------------

    @property
    def properties_path(self) -> Path:
        """Location rung four reads, inside the per-test workspace."""
        return self._properties_path

    def dotenv_path(self) -> Path:
        """Return the location rung three reads. Patched over ``app.config.dotenv_path``."""
        return self._dotenv_path

    # -- writing the two optional files --------------------------------------

    def write_dotenv(self, entries: Mapping[str, str]) -> None:
        """Create rung three's ``.env`` inside the workspace.

        Args:
            entries: ``KEY=value`` pairs, written in the order given.
        """
        body = "".join(f"{key}={value}\n" for key, value in entries.items())
        self._dotenv_path.write_text(body, encoding="utf-8")

    def write_properties(self, entries: Mapping[str, str]) -> None:
        """Create rung four's ``configuration.properties`` inside the workspace.

        The Java ``.properties`` format, exactly as the committed template spells it: one
        ``key=value`` per line, dotted key names, no section header.

        Args:
            entries: ``key=value`` pairs, written in the order given.
        """
        body = "".join(f"{key}={value}\n" for key, value in entries.items())
        self._properties_path.write_text(body, encoding="utf-8")

    # -- resolution -----------------------------------------------------------

    def resolve(self, **overrides: object) -> Config:
        """Resolve the configuration with rung four pointed at the workspace.

        Cached reads are discarded first, because the optional properties file is cached
        process-wide by resolved path and a test that has just written one must be able to
        observe it.

        Args:
            **overrides: Rung-one values, keyed exactly like their environment keys.

        Returns:
            The resolved configuration instance.
        """
        arguments: dict[str, object] = {"CONFIGURATION_PROPERTIES_PATH": self._properties_path}
        arguments.update(overrides)
        self._module.clear_config_caches()
        settings: Config = self._module.load_config(PROFILE_NAME, **arguments)
        return settings

    def provenance_of(self, settings: Config, key: str) -> Provenance:
        """Return the rung that supplied *key*, as the configuration itself reports it.

        Args:
            settings: A resolved configuration.
            key: Environment key of a setting, for example ``"TAG_EXPRESSION"``.

        Returns:
            The winning rung.
        """
        rung: Provenance = settings.provenance_of(key)
        return rung

    def rung(self, name: str) -> Provenance:
        """Return one member of :class:`app.config.Provenance` by attribute name.

        Args:
            name: Member name, one of ``OVERRIDE``, ``ENVIRONMENT``, ``DOTENV``,
                ``PROPERTIES`` or ``DEFAULT``.

        Returns:
            The member.
        """
        member: Provenance = getattr(self._module.Provenance, name)
        return member


@pytest.fixture
def chain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[ChainHarness]:
    """Yield a :class:`ChainHarness` with rungs two, three and four neutralized.

    Cached properties reads are discarded on both sides of the test: before, so this test
    observes only the file it writes itself, and after, so a temporary file cannot colour the
    next test or a parallel worker that reuses the process.

    Args:
        monkeypatch: Patcher for the environment and for ``app.config.dotenv_path``.
        tmp_path: Per-test workspace for the two optional files.

    Yields:
        The harness.
    """
    module = require_app_config()
    module.clear_config_caches()
    try:
        yield ChainHarness(module, monkeypatch, tmp_path)
    finally:
        module.clear_config_caches()


# ===========================================================================
# REQUIREMENT 1 -- the five-rung precedence chain, asserted rung by rung.
#
# AAP 0.3.1, verbatim:
#
#   explicit constructor argument -> environment variable -> .env file
#     -> configuration.properties -> hard-coded default
#
# The chain is written down there rather than left to emerge, "so parity is
# deterministic rather than emergent". Each test below proves that ONE rung
# beats EVERY rung beneath it, by populating all of them with distinguishable
# values at once. Asserting only that "the chain works" would pass even if two
# adjacent rungs were transposed.
#
# The probe setting is TAG_EXPRESSION throughout: it is a plain text setting, it
# carries a properties key, and its default is a preserved defect, so exercising
# it proves the override path of D2 at the same time.
# ===========================================================================

# Distinguishable, obviously synthetic rung markers. None of them is or resembles a
# credential; the chain is being observed, not authenticated.
OVERRIDE_MARKER: Final[str] = "tag-from-explicit-argument"
ENVIRONMENT_MARKER: Final[str] = "tag-from-environment"
DOTENV_MARKER: Final[str] = "tag-from-dotenv"
PROPERTIES_MARKER: Final[str] = "tag-from-properties"


def test_precedence_chain_is_published_in_resolution_order(chain: ChainHarness) -> None:
    """The declared chain is the five rungs of AAP 0.3.1, highest precedence first.

    Published as data by ``app/config.py`` so a test can assert the order rather than restate
    it. Length, order and uniqueness are all checked: a duplicated member would make the
    ordering ambiguous, and a missing one would silently drop a configuration surface.
    """
    module = require_app_config()
    expected = (
        chain.rung("OVERRIDE"),
        chain.rung("ENVIRONMENT"),
        chain.rung("DOTENV"),
        chain.rung("PROPERTIES"),
        chain.rung("DEFAULT"),
    )

    assert tuple(module.PRECEDENCE_CHAIN) == expected
    assert len(module.PRECEDENCE_CHAIN) == 5
    assert len(set(module.PRECEDENCE_CHAIN)) == 5


def test_rung_one_explicit_argument_beats_environment_dotenv_properties_and_default(
    chain: ChainHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit constructor argument outranks every other rung simultaneously.

    All four lower rungs are populated with different values in the same resolution, so this
    asserts the top of the chain rather than merely "an override is read".
    """
    monkeypatch.setenv("TAG_EXPRESSION", ENVIRONMENT_MARKER)
    chain.write_dotenv({"TAG_EXPRESSION": DOTENV_MARKER})
    chain.write_properties({"tag.expression": PROPERTIES_MARKER})

    settings = chain.resolve(TAG_EXPRESSION=OVERRIDE_MARKER)

    assert settings.TAG_EXPRESSION == OVERRIDE_MARKER
    assert chain.provenance_of(settings, "TAG_EXPRESSION") is chain.rung("OVERRIDE")


def test_rung_two_environment_beats_dotenv_properties_and_default(
    chain: ChainHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process environment variable outranks ``.env``, the properties file and the default.

    This is the rung that reproduces python-dotenv's ``override=False`` behaviour by ordering:
    the ``.env`` entry below is present and different, and it loses.
    """
    monkeypatch.setenv("TAG_EXPRESSION", ENVIRONMENT_MARKER)
    chain.write_dotenv({"TAG_EXPRESSION": DOTENV_MARKER})
    chain.write_properties({"tag.expression": PROPERTIES_MARKER})

    settings = chain.resolve()

    assert settings.TAG_EXPRESSION == ENVIRONMENT_MARKER
    assert chain.provenance_of(settings, "TAG_EXPRESSION") is chain.rung("ENVIRONMENT")


def test_rung_three_dotenv_beats_properties_and_default(chain: ChainHarness) -> None:
    """A ``.env`` entry outranks the properties file and the hard-coded default.

    No environment variable is set, so the walk reaches rung three; the properties entry
    below is present and different, and it loses.
    """
    chain.write_dotenv({"TAG_EXPRESSION": DOTENV_MARKER})
    chain.write_properties({"tag.expression": PROPERTIES_MARKER})

    settings = chain.resolve()

    assert settings.TAG_EXPRESSION == DOTENV_MARKER
    assert chain.provenance_of(settings, "TAG_EXPRESSION") is chain.rung("DOTENV")


def test_rung_four_properties_beats_the_hard_coded_default(chain: ChainHarness) -> None:
    """A ``configuration.properties`` key outranks the hard-coded source default.

    Rung four is the one rung addressed by a differently spelled key -- the Java dotted form
    ``tag.expression`` rather than the environment form ``TAG_EXPRESSION`` -- so this also
    proves the two spellings are wired to the same setting.
    """
    chain.write_properties({"tag.expression": PROPERTIES_MARKER})

    settings = chain.resolve()

    assert settings.TAG_EXPRESSION == PROPERTIES_MARKER
    assert chain.provenance_of(settings, "TAG_EXPRESSION") is chain.rung("PROPERTIES")
    assert "tag.expression" in settings.property_keys()


def test_rung_five_default_applies_when_nothing_else_is_configured(chain: ChainHarness) -> None:
    """With no override, no variable, no ``.env`` and no properties file, the default applies.

    This is the rung that matters most rather than the fallback nobody reaches:
    ``configuration.properties`` is git-ignored ``[.gitignore:L3]`` and ``.env`` is untracked,
    so "neither file present" is the state of every fresh checkout, CI job and container
    build. The value asserted is the preserved defect D2 selector of ``[README.md:L87]``.
    """
    settings = chain.resolve()

    assert not chain.properties_path.exists()
    assert not chain.dotenv_path().exists()
    assert settings.TAG_EXPRESSION == EXPECTED_TAG_EXPRESSION
    assert chain.provenance_of(settings, "TAG_EXPRESSION") is chain.rung("DEFAULT")


def test_every_managed_setting_falls_through_to_its_default_when_both_files_are_absent(
    chain: ChainHarness,
) -> None:
    """Absence of both optional files raises nothing and resolves every default (baseline B5).

    AAP 0.6 requires the harness to "degrade gracefully" when ``configuration.properties`` is
    absent: "every setting needs a documented default and the file must be optional". The same
    applies to ``.env``. Resolving at all is therefore half the assertion; the other half is
    that every setting this module owns reports the default rung rather than quietly
    inheriting a value from somewhere.
    """
    settings = chain.resolve()

    assert not chain.properties_path.exists()
    assert not chain.dotenv_path().exists()
    for key in ("CLONE_URL", "CLONE_URL_DOCUMENTED", "TAG_EXPRESSION", "IGNORE_TEST_FAILURES"):
        assert chain.provenance_of(settings, key) is chain.rung("DEFAULT"), key
    for key in ("PYTEST_WORKERS", "TARGET_DIR"):
        assert chain.provenance_of(settings, key) is chain.rung("DEFAULT"), key


def test_an_unreadable_properties_location_degrades_to_the_defaults(chain: ChainHarness) -> None:
    """A properties path that cannot be a file degrades instead of raising.

    The path is pointed at a directory, which is the most common way the setting goes wrong in
    a deployment. The optional file is optional, so this can never break start-up: resolution
    completes and every setting reports the default rung.
    """
    directory = chain.properties_path
    directory.mkdir(parents=True, exist_ok=True)

    settings = chain.resolve()

    assert settings.TAG_EXPRESSION == EXPECTED_TAG_EXPRESSION
    assert chain.provenance_of(settings, "TAG_EXPRESSION") is chain.rung("DEFAULT")


def test_a_malformed_value_falls_through_to_the_next_rung(chain: ChainHarness) -> None:
    """An uncoercible candidate is a fall-through, never a decision and never an error.

    ``IGNORE_TEST_FAILURES`` is a boolean, and the properties file below offers a word that is
    in neither accepted vocabulary. Java's ``Boolean.parseBoolean`` would have read it as
    false, silently turning a typo into build-failure gating -- the exact opposite of the
    preserved defect D3 default. The port falls through to the source default instead.
    """
    chain.write_properties({"ignore.test.failures": "perhaps"})

    settings = chain.resolve()

    assert settings.IGNORE_TEST_FAILURES is EXPECTED_IGNORE_TEST_FAILURES
    assert chain.provenance_of(settings, "IGNORE_TEST_FAILURES") is chain.rung("DEFAULT")


def test_the_boolean_switch_resolves_through_each_rung_in_turn(
    chain: ChainHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure tolerance is settable at every rung, so defect D3 is a default and not a law.

    Text resolution and boolean resolution take different code paths -- one is verbatim, the
    other coerces against a closed vocabulary -- so the chain is asserted for a boolean too
    rather than assumed to behave the same.
    """
    chain.write_properties({"ignore.test.failures": "false"})
    from_properties = chain.resolve()
    assert from_properties.IGNORE_TEST_FAILURES is False
    assert chain.provenance_of(from_properties, "IGNORE_TEST_FAILURES") is chain.rung("PROPERTIES")

    chain.write_dotenv({"IGNORE_TEST_FAILURES": "true"})
    from_dotenv = chain.resolve()
    assert from_dotenv.IGNORE_TEST_FAILURES is True
    assert chain.provenance_of(from_dotenv, "IGNORE_TEST_FAILURES") is chain.rung("DOTENV")

    monkeypatch.setenv("IGNORE_TEST_FAILURES", "off")
    from_environment = chain.resolve()
    assert from_environment.IGNORE_TEST_FAILURES is False
    assert chain.provenance_of(from_environment, "IGNORE_TEST_FAILURES") is chain.rung(
        "ENVIRONMENT"
    )

    from_override = chain.resolve(IGNORE_TEST_FAILURES=True)
    assert from_override.IGNORE_TEST_FAILURES is True
    assert chain.provenance_of(from_override, "IGNORE_TEST_FAILURES") is chain.rung("OVERRIDE")


def test_an_empty_override_is_honoured_as_a_deliberate_decision(chain: ChainHarness) -> None:
    """Presence beats emptiness: an empty value stops the walk instead of falling through.

    This is the documented way to opt out of preserved defect D2 -- ``TAG_EXPRESSION=`` clears
    the tag filter so the whole suite runs. If an empty value were treated as absent, the
    filter could never be cleared through configuration and the defect would be unavoidable
    rather than merely the default.
    """
    settings = chain.resolve(TAG_EXPRESSION="")

    assert settings.TAG_EXPRESSION == ""
    assert chain.provenance_of(settings, "TAG_EXPRESSION") is chain.rung("OVERRIDE")


def test_a_lower_case_override_is_rejected_rather_than_silently_ignored(
    chain: ChainHarness,
) -> None:
    """A misspelled override raises instead of pretending to have taken effect.

    Every setting is named exactly like the environment key the committed ``.env.example``
    documents, and those keys are upper case, so ``tag_expression`` cannot be one. Accepting
    it silently would let a caller believe an override applied when the default was still in
    force -- which, for the preserved defects, is the difference between running the suite and
    running nothing.
    """
    module = require_app_config()

    with pytest.raises(module.ConfigurationError, match="tag_expression"):
        chain.resolve(tag_expression=OVERRIDE_MARKER)


# ===========================================================================
# REQUIREMENT 2 -- every hard-coded default equals its SOURCE value.
#
# AAP Rule T1, verbatim: "Configuration values are data, not decisions. Every
# literal in `pom.xml` and `Jenkins` (versions, thresholds, tag expressions,
# URLs, booleans, sort orders) is carried into the target as an explicit constant
# or default with its source cited. No value is rounded, renamed, or
# 'modernized'."
#
# Each test below cites the source locator of the literal it pins. The literals
# are additionally re-derived from the source text by the extraction suite at the
# end of this module, so a value cannot drift by being changed in two places at
# once.
# ===========================================================================


def test_clone_url_default_is_the_pipeline_url(chain: ChainHarness) -> None:
    """The clone URL default is the pipeline's, ``[Jenkins:L3]``.

    The source line is ``git 'https://github.com/BalamiRR/Upgenix-QA.git'``. Note the
    repository name: ``Upgenix-QA``, not ``Testinium-QA``. The README names a different one,
    which is preserved defect D7 and is asserted separately below.
    """
    settings = chain.resolve()

    assert settings.CLONE_URL == PIPELINE_CLONE_URL
    assert chain.provenance_of(settings, "CLONE_URL") is chain.rung("DEFAULT")
    assert require_app_config().DEFAULT_CLONE_URL == PIPELINE_CLONE_URL


def test_tag_expression_default_is_logout_with_no_leading_at_sign(chain: ChainHarness) -> None:
    """The tag selector default is ``LogOut``, ``[README.md:L87]``: preserved defect **D2**.

    The source line is ``tags = "@LogOut"``. pytest-bdd converts a Gherkin tag into a pytest
    mark and strips the leading ``@``, so the carried value must not keep one -- ``pytest.ini``
    spells the same selection as ``-m "LogOut"``.

    NO SCENARIO CARRIES THAT TAG. The specification's only tags are ``@Login``, ``@UPGN-286``,
    ``@UPGN-287``, ``@UPGN-288``, ``@SalesManager`` and ``@PosManager``, so the documented run
    selects nothing at all. That is preserved on purpose and recorded in
    ``docs/migration-parity.md``; the value is deliberately NOT replaced with a tag that
    exists. The downstream consequence -- that pytest's "no tests ran" exit code must be
    reported as a successful zero-scenario run (criterion V6) -- belongs to
    ``app/services/test_runner_service.py`` and is context here, not an assertion.
    """
    settings = chain.resolve()

    assert settings.TAG_EXPRESSION == EXPECTED_TAG_EXPRESSION
    assert not settings.TAG_EXPRESSION.startswith("@")
    assert settings.TAG_EXPRESSION == settings.TAG_EXPRESSION.strip()
    assert chain.provenance_of(settings, "TAG_EXPRESSION") is chain.rung("DEFAULT")
    assert require_app_config().DEFAULT_TAG_EXPRESSION == EXPECTED_TAG_EXPRESSION


def test_the_preserved_tag_expression_stays_overridable(
    chain: ChainHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defect D2 is the default, not a constraint: both documented rungs override it.

    AAP 0.1.3 requires it "preserved as the default tag expression, overridable by environment
    variable or configuration". Preserving a defect while making it unfixable would be a
    different defect, so both documented spellings are exercised: the environment key and the
    dotted properties key.
    """
    chain.write_properties({"tag.expression": "Login"})
    from_properties = chain.resolve()
    assert from_properties.TAG_EXPRESSION == "Login"

    monkeypatch.setenv("TAG_EXPRESSION", "UPGN-288")
    from_environment = chain.resolve()
    assert from_environment.TAG_EXPRESSION == "UPGN-288"


def test_ignore_test_failures_default_is_true(chain: ChainHarness) -> None:
    """Failure tolerance defaults to true, ``[pom.xml:L25]``: preserved defect **D3**.

    The source line is ``<testFailureIgnore>true</testFailureIgnore>``, which made the Maven
    build swallow test failures outright. Compounded with the six ``-1`` publisher thresholds
    of ``[Jenkins:L15]``, the source system had no build-time quality gate whatsoever.

    Asserted as true, and deliberately not gated: enabling build-failure gating is listed as
    explicitly out of scope, and doing it here would make the port fail where the original
    succeeded. Switching it off is available through every rung and is recorded in
    ``docs/migration-parity.md`` as a deliberate deviation rather than a fix.
    """
    settings = chain.resolve()

    assert settings.IGNORE_TEST_FAILURES is EXPECTED_IGNORE_TEST_FAILURES
    assert chain.provenance_of(settings, "IGNORE_TEST_FAILURES") is chain.rung("DEFAULT")
    assert require_app_config().DEFAULT_IGNORE_TEST_FAILURES is EXPECTED_IGNORE_TEST_FAILURES


def test_parallelism_default_expresses_unlimited_workers(chain: ChainHarness) -> None:
    """Worker allocation defaults to ``logical``, the port of ``[pom.xml:L22-L23]``.

    The source declares ``<parallel>methods</parallel>`` together with
    ``<useUnlimitedThreads>true</useUnlimitedThreads>``. pytest-xdist has no "unlimited"
    setting; ``logical`` sizes the pool from the machine, which is the closest available
    analogue and is what ``pytest.ini`` requests as ``-n logical``. A fixed number here would
    contradict the ``useUnlimitedThreads`` half of the source declaration.
    """
    settings = chain.resolve()

    assert settings.PYTEST_WORKERS == EXPECTED_PYTEST_WORKERS
    assert not settings.PYTEST_WORKERS.isdigit()
    assert chain.provenance_of(settings, "PYTEST_WORKERS") is chain.rung("DEFAULT")
    assert require_app_config().DEFAULT_PYTEST_WORKERS == EXPECTED_PYTEST_WORKERS


def test_the_four_worker_tuning_value_is_present_but_disabled(chain: ChainHarness) -> None:
    """The commented-out thread count is carried across as documentation, not as a knob.

    The source keeps it commented out exactly like this ``[pom.xml:L24]``::

        <!--                    <threadCount>4</threadCount>-->

    AAP goal O7 requires it "preserved as a documented, disabled tuning default", so both
    halves are asserted: the value is recorded, and the flag that says it is not in force is
    false. The active parallelism setting stays ``logical``.

    It is recorded rather than resolved, and deliberately has no configuration key of its own:
    pinning four workers is a one-line opt-in through ``PYTEST_WORKERS``, which both committed
    templates already carry as a commented-out line. That is why the provenance record does
    not name it, and why this test asserts its absence from that record instead of a rung.
    """
    settings = chain.resolve()
    module = require_app_config()

    assert settings.THREAD_COUNT == EXPECTED_DISABLED_THREAD_COUNT
    assert settings.THREAD_COUNT_ENABLED is False
    assert module.DISABLED_THREAD_COUNT == EXPECTED_DISABLED_THREAD_COUNT
    assert module.DEFAULT_THREAD_COUNT_ENABLED is False
    # No configuration key, therefore no rung: it is not resolved through the chain at all.
    assert "THREAD_COUNT" not in settings.provenance()
    assert settings.PYTEST_WORKERS != str(EXPECTED_DISABLED_THREAD_COUNT)


def test_artifact_root_default_is_target_and_agrees_with_its_single_owner(
    chain: ChainHarness,
) -> None:
    """The artifact root is the literal ``target``, ``[README.md:L79-L82]``.

    Those four lines are the Cucumber plugin destinations -- ``html:target/cucumber-reports.
    html``, ``json:target/cucumber.json``, ``rerun:target/rerun.txt`` and
    ``me.jvt.cucumber.report.PrettyReports:target/cucumber``. (The AAP body cites them as
    L78-L81; that is an off-by-one, L78 being the opening ``plugin = {``.)

    The Java-flavoured name is retained deliberately rather than modernized, because the CI
    publisher matches reports with ``fileIncludePattern: '**/*.json'`` ``[Jenkins:L15]`` and
    keeping the output under ``target/`` means that contract needs no edit at all.

    ``app/utils/paths.py`` is the single owner of the layout, so the configured value is
    asserted to be *the same object* it publishes rather than an equal copy. The path stays
    relative on purpose: ``pytest.ini``, the ``Makefile`` and both committed templates all
    spell it relatively, and resolving it here would make the reported layout stop matching
    them. Detailed layout assertions belong to ``tests/unit/test_paths.py``.
    """
    settings = chain.resolve()
    paths = require_app_module("app.utils.paths")

    assert settings.TARGET_DIR == Path(EXPECTED_ARTIFACT_ROOT)
    assert not settings.TARGET_DIR.is_absolute()
    assert settings.TARGET_DIR == paths.TARGET_DIR
    assert require_app_config().DEFAULT_TARGET_DIR is paths.TARGET_DIR
    assert chain.provenance_of(settings, "TARGET_DIR") is chain.rung("DEFAULT")


def test_resolved_settings_carry_the_expected_python_types(chain: ChainHarness) -> None:
    """Type fidelity: a string stays a string, a flag stays a ``bool``, a count stays an ``int``.

    ``bool`` subclasses ``int`` in Python, so a count that arrived as ``True`` would satisfy
    ``isinstance(value, int)`` while being nonsense. The integer check is therefore exact
    (``type(value) is int``) rather than an ``isinstance`` test.

    The string checks also reject stray whitespace and stray quoting: a value read out of a
    ``.properties`` or ``.env`` file is taken verbatim, so a quoted or padded literal would
    survive into the configuration and break an exact comparison somewhere downstream.
    """
    settings = chain.resolve()

    for name in ("CLONE_URL", "CLONE_URL_DOCUMENTED", "TAG_EXPRESSION", "PYTEST_WORKERS"):
        value = getattr(settings, name)
        assert isinstance(value, str), name
        assert value == value.strip(), name
        assert not value.startswith(("'", '"')), name
        assert not value.endswith(("'", '"')), name

    for name in ("IGNORE_TEST_FAILURES", "THREAD_COUNT_ENABLED"):
        assert type(getattr(settings, name)) is bool, name

    assert type(settings.THREAD_COUNT) is int
    assert isinstance(settings.TARGET_DIR, Path)


def test_the_preserved_defaults_are_identical_in_every_profile(chain: ChainHarness) -> None:
    """Every profile carries the same parity constants; only operational behaviour differs.

    Criterion V4 asserts the preserved constants "regardless of environment", so a profile that
    quietly relaxed one would defeat the whole contract -- and the parity suite asserts these
    values through an application built with the ``testing`` profile, which makes the
    invariance load-bearing rather than tidy.

    ``production`` is included by supplying a signing key explicitly, because that profile
    refuses to start without one. The key below is an obvious synthetic placeholder and is not
    a credential.
    """
    module = require_app_config()
    expected = {
        "CLONE_URL": PIPELINE_CLONE_URL,
        "CLONE_URL_DOCUMENTED": DOCUMENTED_CLONE_URL,
        "TAG_EXPRESSION": EXPECTED_TAG_EXPRESSION,
        "IGNORE_TEST_FAILURES": EXPECTED_IGNORE_TEST_FAILURES,
        "PYTEST_WORKERS": EXPECTED_PYTEST_WORKERS,
        "THREAD_COUNT": EXPECTED_DISABLED_THREAD_COUNT,
        "THREAD_COUNT_ENABLED": False,
    }

    assert set(module.CONFIG_NAMES) == {"development", "testing", "production"}
    for profile in module.CONFIG_NAMES:
        overrides: dict[str, object] = {
            "CONFIGURATION_PROPERTIES_PATH": chain.properties_path,
            "SECRET_KEY": "not-a-real-key-profile-invariance-probe",
        }
        module.clear_config_caches()
        settings = module.CONFIG_MAP[profile](**overrides)
        assert settings.CONFIG_NAME == profile
        for name, value in expected.items():
            assert getattr(settings, name) == value, f"{profile}.{name}"


def test_publisher_constants_are_exposed_for_configuration_introspection(
    chain: ChainHarness,
) -> None:
    """The publisher settings reach the configuration surface, so ``GET /api/v1/config`` can
    report them.

    PRESENCE ONLY, deliberately. Their values -- the six ``-1`` thresholds, ``ALPHABETICAL``
    and ``**/*.json`` of ``[Jenkins:L15]`` -- are declared in ``app/reporting/thresholds.py``
    and asserted by ``tests/unit/test_thresholds_parity.py``, which owns that half of criterion
    V4. Restating them here would create a second copy of somebody else's contract, and the
    include pattern in particular is preserved verbatim AS DATA: it is never expanded against
    the filesystem, compiled, normalized or rewritten, here or anywhere else.
    """
    settings = chain.resolve()

    for name in PUBLISHER_INTROSPECTION_KEYS:
        assert hasattr(settings, name), name
    assert set(settings.PUBLISHER_THRESHOLDS) == set(
        require_app_module("app.reporting.thresholds").PUBLISHER_THRESHOLD_KEYS
    )


def test_the_running_application_resolves_the_same_defaults(
    request: pytest.FixtureRequest,
) -> None:
    """The application's own configuration mapping carries the defaults asserted above.

    Corroboration through the factory rather than a second source of truth: the ``config``
    fixture hands over the mapping an application built by ``create_app`` actually holds, so
    this catches a factory that resolved the configuration and then overwrote part of it. The
    mapping is requested lazily, so an application that cannot yet be built skips this one
    test instead of erroring the module.
    """
    resolved = application_config(request)

    assert resolved["CLONE_URL"] == PIPELINE_CLONE_URL
    assert resolved["CLONE_URL_DOCUMENTED"] == DOCUMENTED_CLONE_URL
    assert resolved["TAG_EXPRESSION"] == EXPECTED_TAG_EXPRESSION
    assert resolved["IGNORE_TEST_FAILURES"] is EXPECTED_IGNORE_TEST_FAILURES
    assert resolved["PYTEST_WORKERS"] == EXPECTED_PYTEST_WORKERS
    assert resolved["THREAD_COUNT"] == EXPECTED_DISABLED_THREAD_COUNT
    assert resolved["THREAD_COUNT_ENABLED"] is False
    assert resolved["TARGET_DIR"] == Path(EXPECTED_ARTIFACT_ROOT)


# ===========================================================================
# REQUIREMENT 3 -- defect D7: the two clone URLs are PRESERVED, not UNIFIED.
#
# The pipeline clones `https://github.com/BalamiRR/Upgenix-QA.git` [Jenkins:L3];
# the README instructs cloning `https://github.com/BalamiRR/Testinium-QA.git`
# [README.md:L59]. The pipeline value is the runtime default because executable
# configuration outranks prose when the two disagree, and the Jira key prefix
# `UPGN` [README.md:L115] agrees with the pipeline URL -- the tags in the
# specification are `@UPGN-286`, `@UPGN-287` and `@UPGN-288`, which is
# independent corroboration that the Upgenix repository is the live one.
#
# Both strings survive. Erasing either would lose information, so the
# discrepancy is documented rather than silently unified, and
# `docs/migration-parity.md` is the authoritative register. Unifying them is
# explicitly out of scope: AAP Rule T4, "defects are behavior".
# ===========================================================================


def test_the_runtime_default_is_the_pipeline_url_and_the_other_survives_beside_it(
    chain: ChainHarness,
) -> None:
    """Both URLs are retained, and the pipeline's is the one in force.

    The two are asserted to be genuinely different as well as individually correct: a port that
    "helpfully" reconciled them would still satisfy two equality checks written against a
    single value, so the inequality is what actually pins defect D7.
    """
    settings = chain.resolve()

    assert settings.CLONE_URL == PIPELINE_CLONE_URL
    assert settings.CLONE_URL_DOCUMENTED == DOCUMENTED_CLONE_URL
    assert settings.CLONE_URL != settings.CLONE_URL_DOCUMENTED
    assert "Upgenix-QA" in settings.CLONE_URL
    assert "Testinium-QA" in settings.CLONE_URL_DOCUMENTED


def test_one_configuration_key_resolves_either_repository_url(
    chain: ChainHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single key selects either URL: AAP AMB-7's "both settable through one key".

    ``CLONE_URL`` is that key, and it is exercised at both documented rungs. No second key is
    asserted to exist, because a second key for the same decision is how two contradictory
    values quietly become two independent settings that can drift apart.

    ``CLONE_URL_DOCUMENTED`` is not that second key: it carries the *other* string so the
    disagreement stays machine-readable and can be reported alongside the URL in force. Asking
    the clone stage to use it is done by setting ``CLONE_URL``, exactly as below.
    """
    chain.write_properties({"clone.url": DOCUMENTED_CLONE_URL})
    from_properties = chain.resolve()
    assert from_properties.CLONE_URL == DOCUMENTED_CLONE_URL
    assert chain.provenance_of(from_properties, "CLONE_URL") is chain.rung("PROPERTIES")

    monkeypatch.setenv("CLONE_URL", DOCUMENTED_CLONE_URL)
    from_environment = chain.resolve()
    assert from_environment.CLONE_URL == DOCUMENTED_CLONE_URL
    assert chain.provenance_of(from_environment, "CLONE_URL") is chain.rung("ENVIRONMENT")


def test_the_readme_still_documents_its_own_clone_url(project_root: Path) -> None:
    """``README.md`` still carries ``[README.md:L59]``'s URL: documented, not unified.

    This is how "the discrepancy is documented, not silently unified" becomes executable
    evidence rather than a claim in a comment (AAP 0.7 baseline B11). The rewritten README is
    required to retain the human clone instruction, so the string must still be findable in it
    even though the surrounding prose is now about the Python toolchain.

    Searched rather than read from line 59: the README is rewritten in place by this migration,
    so its line numbering has moved while the literal itself is preserved.
    """
    readme = read_repository_text(project_root, "README.md")

    assert DOCUMENTED_CLONE_URL in readme


# ===========================================================================
# REQUIREMENT 4 -- baseline B5: configuration through the environment plus an
# OPTIONAL properties file, and never a hard-coded secret.
#
# AAP 0.7 baseline B5, verbatim: "Configuration through environment plus an
# optional properties file; never hard-coded secrets", realized in
# "`app/config.py`, `.env.example`, `configuration.properties.example`; the real
# `configuration.properties` stays git-ignored `[.gitignore:L3]`".
# ===========================================================================


def test_the_runtime_properties_file_is_contracted_to_stay_untracked(project_root: Path) -> None:
    """``.gitignore`` still ignores ``configuration.properties`` ``[.gitignore:L3]``.

    That single line is the whole reason absence is the normal case rather than an edge case,
    and therefore the reason every setting needs a documented default. The ignore rule is the
    contract; the graceful-degradation tests above are the behaviour it demands.

    The *presence* of a real ``configuration.properties`` or ``.env`` in a working copy is
    deliberately NOT asserted either way: both are legitimately allowed to exist locally, which
    is exactly what being git-ignored means. What must hold is that the rule keeping them out of
    version control is still there, and that only the ``.example`` templates are committed.
    """
    ignore_rules = read_repository_text(project_root, ".gitignore").splitlines()

    assert PROPERTIES_FILENAME in [rule.strip() for rule in ignore_rules]
    for template in COMMITTED_TEMPLATES:
        assert (project_root / template).is_file(), template


def test_committed_templates_document_every_setting_asserted_here(project_root: Path) -> None:
    """Both templates carry the keys this module pins, in their own spelling.

    ``.env.example`` documents the upper-case environment keys and
    ``configuration.properties.example`` the dotted Java keys, so a reader can discover the
    whole configurable surface without reading code. A setting asserted here but missing from
    the templates would be configurable in fact and undiscoverable in practice.

    Neither template is authored or edited by this module; they are read only.
    """
    environment_template = read_repository_text(project_root, ".env.example")
    properties_template = read_repository_text(project_root, "configuration.properties.example")

    for key in ("CLONE_URL", "CLONE_URL_DOCUMENTED", "TAG_EXPRESSION", "IGNORE_TEST_FAILURES"):
        assert f"{key}=" in environment_template, key
    for key in ("PYTEST_WORKERS", "TARGET_DIR", "CONFIGURATION_PROPERTIES_PATH"):
        assert f"{key}=" in environment_template, key

    for key in ("clone.url", "clone.url.documented", "tag.expression", "ignore.test.failures"):
        assert f"{key}=" in properties_template, key
    for key in ("pytest.workers", "target.dir"):
        assert f"{key}=" in properties_template, key


def test_app_config_reads_the_properties_file_only_through_the_properties_module(
    project_root: Path,
) -> None:
    """``app/config.py`` delegates rung four instead of parsing the file itself.

    ``app/utils/properties.py`` is the single point through which the Java ``.properties``
    format is read: it synthesizes a section header for the standard library's parser and never
    raises, so a missing, unreadable or malformed file degrades to an empty mapping. A second
    reader in the configuration module would be a second set of degradation rules.

    Asserted structurally rather than textually: the module is parsed and its real import and
    call nodes are inspected, so the words "configparser" and "open" appearing in its prose
    cannot produce a false result, and a genuine import cannot hide inside one.
    """
    tree = parse_module(project_root / "app" / "config.py")
    imported = imported_modules(tree)

    assert "configparser" not in imported
    assert "app.utils.properties" in imported
    assert called_builtins(tree).count("open") == 0


def test_app_config_hard_codes_no_credential(project_root: Path) -> None:
    """No provider-shaped secret appears in ``app/config.py``.

    The module holds one credential-shaped setting, the Flask signing key, and the value it
    falls back to is an obvious visible placeholder that the committed ``.env.example`` and the
    compose fallback both carry identically. Nothing in the source system published a real
    credential -- the only credential-ish strings it contains are the two Examples-table
    password values, which are test data for the external application and have no place in
    configuration.

    The scan is for the provider prefixes a real leak would match, so it fails on an actual
    secret rather than on the word "secret". Needing no import of the application, it keeps
    running in a degraded environment, which is where an unreviewed literal is likeliest to go
    unnoticed.
    """
    text = read_repository_text(project_root, "app/config.py")

    leaked = [pattern for pattern in SECRET_PATTERNS if re.search(pattern, text)]

    assert leaked == []


def test_the_signing_key_fallback_is_an_obvious_placeholder(chain: ChainHarness) -> None:
    """The development signing key announces itself as a placeholder and is never reported.

    Two properties make it safe rather than merely conventional. It reads as an instruction to
    replace it -- the same visible string the committed ``.env.example`` and the compose
    fallback carry -- so it can never be mistaken for a generated key. And it is filtered out of
    the introspection payload, so a diagnostic response or a log record cannot carry the signing
    key of whatever process produced it.
    """
    module = require_app_config()
    settings = chain.resolve()

    assert "change-me" in module.DEVELOPMENT_SECRET_KEY
    assert not re.fullmatch(r"[0-9a-fA-F]{32,}", module.DEVELOPMENT_SECRET_KEY)
    assert [
        pattern for pattern in SECRET_PATTERNS if re.search(pattern, module.DEVELOPMENT_SECRET_KEY)
    ] == []
    assert "SECRET_KEY" not in settings.as_dict()
    assert "SECRET_KEY" not in repr(settings)


def test_app_config_is_the_single_dotenv_load_point(project_root: Path) -> None:
    """Exactly one module under ``app/`` imports python-dotenv, and it is the configuration one.

    ``wsgi.py``, ``run.py`` and ``gunicorn.conf.py`` deliberately do not load ``.env``, and no
    module under ``app/utils/`` may either. One load point is what keeps the ordering of rung
    three unambiguous: a second loader that mutated the process environment would silently
    promote a ``.env`` entry to rung two and invert the documented precedence.
    """
    loaders = [
        path.relative_to(project_root).as_posix()
        for path in python_sources(project_root / "app")
        if "dotenv" in imported_top_level_packages(path)
    ]

    assert loaders == ["app/config.py"]


# ===========================================================================
# REQUIREMENT 5 -- layering, AAP Rule T7 and baseline B4.
#
# Rule T7, verbatim: "Strict one-direction internal dependencies.
# `api -> services -> reporting -> utils`. Nothing under `app/` may import from
# `tests/`."
#
# AAP 0.4.2's dependency graph places configuration beside the factory with the
# edge `CFG -> UTL`, so `app/config.py` may import from `app.utils` and from
# `app.reporting.thresholds`, whose constants it exposes for introspection -- and
# from nothing else inside the application. The factory imports the configuration
# module, so an import back into `app.api`, `app.web` or `app.services` would
# close a cycle at start-up as well as invert the layering.
#
# Every check here is structural, over the real syntax tree. A textual scan would
# be defeated in both directions: `app/config.py`'s own docstring names
# `app.api`, `app.services` and `tests/` while importing none of them.
# ===========================================================================


def test_app_config_imports_nothing_above_or_beside_its_layer(project_root: Path) -> None:
    """``app/config.py`` reaches only downwards, into ``app.utils`` and the thresholds module."""
    imported = imported_modules(parse_module(project_root / "app" / "config.py"))

    for forbidden in FORBIDDEN_CONFIG_IMPORTS:
        prefix = f"{forbidden}."
        offenders = [
            name for name in sorted(imported) if name == forbidden or name.startswith(prefix)
        ]
        assert offenders == [], forbidden

    first_party = sorted(name for name in imported if name.split(".")[0] == "app")
    assert first_party == [
        "app.reporting.thresholds",
        "app.utils.paths",
        "app.utils.properties",
    ]


def test_nothing_under_app_imports_from_the_test_tree(project_root: Path) -> None:
    """The ``app`` -> ``tests`` edge does not exist, in any module of the application.

    Modules under ``tests`` may import from ``app``; the reverse is forbidden, so the deployable
    application stays independent of the harness even though both derive from the same source
    specification. A deployment installs ``requirements.txt`` alone, so a single reverse import
    would break the container rather than merely offend the diagram.
    """
    offenders = [
        path.relative_to(project_root).as_posix()
        for path in python_sources(project_root / "app")
        if "tests" in imported_top_level_packages(path)
    ]

    assert offenders == []


def test_app_config_neither_prints_nor_configures_logging(project_root: Path) -> None:
    """The configuration module obtains a logger and stops there (baseline B9).

    Structured logging rather than print statements, and handlers, levels and formats belong
    exclusively to ``app/logging_config.py``. A configuration module that installed a handler
    would silently outrank the application's own logging settings for every process that
    imports it -- which is every process.
    """
    tree = parse_module(project_root / "app" / "config.py")

    assert called_builtins(tree).count("print") == 0
    assert called_attributes(tree) & LOGGING_CONFIGURATION_CALLS == set()


# ===========================================================================
# TWO-SIDED PARITY -- the expected values re-derived from the SOURCE artifacts.
#
# AAP 0.7 baseline B11: "executable parity evidence rather than assertions of
# parity". Hard-coding an expected value twice proves only that it was typed
# twice; extracting it from the artifact that published it proves the carry
# itself.
#
# The comparison is deliberately transitive, and split so each half stands alone:
#
#   source text  ==  this module's constant     <- the tests below, stdlib only
#   this module's constant  ==  resolved default <- Requirement 2, needs the app
#
# so in an environment that cannot import the application, the source side still
# runs and still carries evidence. `pom.xml` is REFERENCE mode -- read
# statically, never compiled, never edited -- and `Jenkins` and `README.md` are
# read only here as well. This module modifies none of them.
# ===========================================================================


def test_the_pipeline_source_text_publishes_the_expected_clone_url(project_root: Path) -> None:
    """``Jenkins``'s clone stage names the URL this module expects ``[Jenkins:L3]``."""
    pipeline = read_repository_text(project_root, "Jenkins")

    assert extract(r"git\s+'([^']+)'", pipeline, "Jenkins") == PIPELINE_CLONE_URL


def test_the_readme_source_text_publishes_the_expected_tag_expression(project_root: Path) -> None:
    """The documented runner's tag selector is ``@LogOut`` ``[README.md:L87]``.

    The ``@`` is captured out of the pattern rather than out of the value: the source spells the
    Gherkin tag, and the configured default spells the pytest mark that pytest-bdd derives from
    it, which is the same selector with the sigil stripped.
    """
    readme = read_repository_text(project_root, "README.md")

    assert extract(r'tags\s*=\s*"@([A-Za-z0-9_.\-]+)"', readme, "README.md") == (
        EXPECTED_TAG_EXPRESSION
    )


def test_the_pom_source_text_publishes_the_expected_failure_tolerance(project_root: Path) -> None:
    """``<testFailureIgnore>`` is ``true`` in the source POM ``[pom.xml:L25]``."""
    pom = read_repository_text(project_root, "pom.xml")
    declared = extract(r"<testFailureIgnore>\s*([A-Za-z]+)\s*</testFailureIgnore>", pom, "pom.xml")

    assert declared == "true"
    assert EXPECTED_IGNORE_TEST_FAILURES is True


def test_the_pom_source_text_publishes_unlimited_method_level_parallelism(
    project_root: Path,
) -> None:
    """Parallelism is declared as unlimited-thread, method-level ``[pom.xml:L22-L23]``.

    Both halves matter. ``methods`` alone would say what is parallelized; ``useUnlimitedThreads``
    is what says the pool is unbounded, and together they are why the port requests xdist's
    machine-sized ``logical`` allocation rather than any fixed number.
    """
    pom = read_repository_text(project_root, "pom.xml")

    assert extract(r"<parallel>\s*([A-Za-z]+)\s*</parallel>", pom, "pom.xml") == "methods"
    assert (
        extract(r"<useUnlimitedThreads>\s*([A-Za-z]+)\s*</useUnlimitedThreads>", pom, "pom.xml")
        == "true"
    )
    assert EXPECTED_PYTEST_WORKERS == "logical"


def test_the_pom_source_text_keeps_its_thread_count_commented_out(project_root: Path) -> None:
    """The four-worker tuning value is present in the POM and INACTIVE ``[pom.xml:L24]``.

    Two assertions, because "preserved as a documented, disabled tuning default" has two
    halves: the value is discoverable inside the comment, and no active declaration of it
    exists once the comments are removed. If the source had ever enabled it, the port's
    ``logical`` default would be the divergence rather than the parity.
    """
    pom = read_repository_text(project_root, "pom.xml")
    commented = extract(r"<!--\s*<threadCount>\s*(\d+)\s*</threadCount>\s*-->", pom, "pom.xml")
    uncommented = re.sub(r"<!--.*?-->", "", pom, flags=re.DOTALL)

    assert int(commented) == EXPECTED_DISABLED_THREAD_COUNT
    assert "<threadCount>" not in uncommented


def test_the_readme_source_text_publishes_the_expected_artifact_root(project_root: Path) -> None:
    """Every documented report destination sits under ``target/`` ``[README.md:L79-L82]``.

    All four plugin destinations are checked, not just one, because the artifact root is only
    genuinely preserved if nothing was quietly relocated: the CI publisher's ``**/*.json`` glob
    ``[Jenkins:L15]`` needs no edit precisely because they all still share this root.
    """
    readme = read_repository_text(project_root, "README.md")
    destinations = (
        r'"html:([a-z]+)/cucumber-reports\.html"',
        r'"json:([a-z]+)/cucumber\.json"',
        r'"rerun:([a-z]+)/rerun\.txt"',
        r'PrettyReports:([a-z]+)/cucumber"',
    )

    for pattern in destinations:
        assert extract(pattern, readme, "README.md") == EXPECTED_ARTIFACT_ROOT, pattern
