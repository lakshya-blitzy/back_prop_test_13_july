"""Canonical layout of the ``target/`` artifact tree.

This module is the single source of truth for *where* every report artifact the
project produces is written, and it owns the creation of the directories those
artifacts live in. It owns nothing else.

It is deliberately the terminal node of the application's dependency chain
(``api -> services -> reporting -> utils``): it imports only the Python
standard library, so it can never participate in an import cycle and can never
drag a third-party package into a deployment that installs ``requirements.txt``
alone.

Why this module exists
----------------------
The Java/Maven original declared its four report artifacts inline in the
Cucumber runner's ``@CucumberOptions.plugin`` array, and Maven implicitly
created ``target/`` as part of its build lifecycle. The Python port has neither
of those things: the Cucumber-JSON writer supplied by ``pytest-bdd`` does **not**
create the parent directory of the file it is asked to write, so a run started
while ``target/`` is absent fails at session finish with ``FileNotFoundError``.
Closing that trap is this module's headline responsibility, and
:func:`ensure_target_layout` is the function that closes it. Directory creation
is guaranteed in three independent places on purpose -- here, in the
``Makefile`` ``test``/``dirs`` targets, and in the BDD ``conftest.py`` -- so that
it cannot be missed.

Why the directory is still called ``target``
--------------------------------------------
The CI pipeline publishes the reports with::

    cucumber failedFeaturesNumber: -1, failedScenariosNumber: -1,
    failedStepsNumber: -1, fileIncludePattern: '**/*.json',
    pendingStepsNumber: -1, skippedStepsNumber: -1,
    sortingMethod: 'ALPHABETICAL', undefinedStepsNumber: -1

``[Jenkins:L15]``. Because the ported application keeps writing underneath
``target/``, that publisher invocation needs no change whatsoever -- which is the
entire payoff of retaining the Java-flavoured directory name. Renaming the root
to ``build``, ``out``, ``dist``, ``reports`` or ``artifacts`` would silently
break CI report publication, so the name is a hard requirement rather than a
preference. The include pattern itself is intentionally *not* declared in this
module: it belongs to ``app/reporting/thresholds.py`` next to the six publisher
thresholds. This module's obligation is simply never to invalidate it.

Source of truth
---------------
Every path literal below is carried over verbatim from the two files this module
ports, and is cited at its point of definition (configuration values are data,
never decisions -- nothing here is renamed or modernised):

* ``[README.md:L79-L82]`` -- the four ``@CucumberOptions`` plugin declarations::

      "html:target/cucumber-reports.html",
      "json:target/cucumber.json",
      "rerun:target/rerun.txt",
      "me.jvt.cucumber.report.PrettyReports:target/cucumber"

  (The Agent Action Plan cites this block as ``L78-L81``; the verified
  locations in the current file are ``L79-L82``. The literals are identical
  either way and the literals are what matter.)
* ``[README.md:L42-L43]`` -- "It generate JSON, HTML and Txt reporters as well.
  It also generate ``screen shots`` for your tests if you enable it and also
  generate ``error shots`` for your failed test cases as well." -- the origin of
  ``target/screenshots/`` and ``target/error-shots/``.
* ``[Jenkins:L15]`` -- the publisher invocation quoted above, which is what makes
  the ``target/`` root immutable.

``target/surefire-reports/`` has no README counterpart. Its name is the Surefire
one, retained for report-consumer parity so that any downstream consumer keyed
on that path keeps finding content; it is populated by the JUnit-XML output the
test runner requests.

Scope boundaries -- deliberate omissions
----------------------------------------
* **No log path.** ``target/`` is wiped on every run, so a log file placed there
  would vanish. The log location is owned by ``app/logging_config.py``.
* **No deletion.** Wiping ``target/`` is the ``Makefile`` ``clean`` target's job
  (the port of ``mvn clean``). This module contributes layout *knowledge* only:
  it creates directories and never removes a directory or a file, so no
  tree-removal, file-deletion or directory-removal call appears anywhere below.
* **No environment access and no process execution.** Configuration precedence
  (constructor argument -> environment variable -> ``.env`` ->
  ``configuration.properties`` -> default) is ``app/config.py``'s
  responsibility, and process execution is ``app/services/``'s.
* **No file I/O.** This module only composes paths and creates directories: it
  never reads or writes a file's contents. The project-wide "always pass
  ``encoding='utf-8'``" requirement therefore has nothing to apply to here, and
  no byte or text stream is ever opened below.
* **No import-time side effects.** Importing this module only defines constants.
  Directories are created if and only if a caller invokes
  :func:`ensure_target_layout`, so mere module discovery -- by ``ruff``, by
  ``mypy``, or by a ``pytest`` collection pass -- never touches the filesystem.

Usage
-----
::

    >>> from app.utils import paths
    >>> paths.CUCUMBER_JSON_PATH.as_posix()
    'target/cucumber.json'
    >>> created = paths.ensure_target_layout()   # the five directories
    >>> len(created)
    5
    >>> layout = paths.resolve_layout(base)      # same layout, another base
"""

import logging
import os
from pathlib import Path
from typing import Final, NamedTuple, Self

__all__ = [
    "CUCUMBER_HTML_NAME",
    "CUCUMBER_HTML_PATH",
    "CUCUMBER_JSON_NAME",
    "CUCUMBER_JSON_PATH",
    "DEFAULT_LAYOUT",
    "ERROR_SHOTS_DIR",
    "ERROR_SHOTS_DIR_NAME",
    "MANAGED_DIRECTORIES",
    "PRETTY_REPORTS_DIR",
    "PRETTY_REPORTS_DIR_NAME",
    "REPORT_FILES",
    "RERUN_TXT_NAME",
    "RERUN_TXT_PATH",
    "SCREENSHOTS_DIR",
    "SCREENSHOTS_DIR_NAME",
    "SUREFIRE_JUNIT_XML_NAME",
    "SUREFIRE_JUNIT_XML_PATH",
    "SUREFIRE_REPORTS_DIR",
    "SUREFIRE_REPORTS_DIR_NAME",
    "TARGET_DIR",
    "TARGET_DIR_NAME",
    "TARGET_SUBDIRS",
    "TARGET_SUBDIR_NAMES",
    "StrPath",
    "TargetLayout",
    "artifact_paths",
    "ensure_layout",
    "ensure_parent_directory",
    "ensure_target_directories",
    "ensure_target_dirs",
    "ensure_target_layout",
    "managed_directories",
    "resolve_layout",
    "target_root",
    "to_posix",
]

# A module logger, and nothing more. Handlers, levels and formatters are
# configured exclusively by `app/logging_config.py`: this module neither
# configures the logging system nor writes to standard output, so every message
# it emits is a structured, lazily-formatted log record on this logger.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

type StrPath = str | os.PathLike[str]
"""Anything :class:`pathlib.Path` accepts as a single path component."""


# =============================================================================
# Name literals.
#
# Every value below is quoted verbatim from its cited source. The artifact root
# is `target` and must never be renamed: the CI publisher's
# fileIncludePattern glob at [Jenkins:L15] keeps working precisely because the
# output stays underneath this directory.
# =============================================================================

# [README.md:L79-L82] the shared `target/` prefix of all four plugin outputs.
# [Jenkins:L15] is why it can never be renamed.
TARGET_DIR_NAME: Final[str] = "target"
"""Name of the ephemeral artifact root. Never rename this."""

# [README.md:L79] "html:target/cucumber-reports.html"
CUCUMBER_HTML_NAME: Final[str] = "cucumber-reports.html"
"""File name of the Cucumber HTML report."""

# [README.md:L80] "json:target/cucumber.json" -- also the file the CI publisher
# selects with its fileIncludePattern glob [Jenkins:L15].
CUCUMBER_JSON_NAME: Final[str] = "cucumber.json"
"""File name of the Cucumber JSON report."""

# [README.md:L81] "rerun:target/rerun.txt"
RERUN_TXT_NAME: Final[str] = "rerun.txt"
"""File name of the rerun manifest."""

# [README.md:L82] "me.jvt.cucumber.report.PrettyReports:target/cucumber"
PRETTY_REPORTS_DIR_NAME: Final[str] = "cucumber"
"""Directory name of the PrettyReports output."""

# [README.md:L42-L43] "It also generate `screen shots` for your tests if you
# enable it ..."
SCREENSHOTS_DIR_NAME: Final[str] = "screenshots"
"""Directory name of the screen shots."""

# [README.md:L42-L43] "... also generate `error shots` for your failed test
# cases as well."
ERROR_SHOTS_DIR_NAME: Final[str] = "error-shots"
"""Directory name of the error shots captured for failed test cases."""

# No README counterpart: the Surefire directory name is retained verbatim for
# report-consumer parity, so anything keyed on that path keeps finding content.
SUREFIRE_REPORTS_DIR_NAME: Final[str] = "surefire-reports"
"""Directory name of the Surefire-compatible report output."""

# `TEST-<class>.xml` is Surefire's own naming convention, and `CukesRunner` is
# the runner the source build collected via `**/CukesRunner*.java` [pom.xml:L27].
SUREFIRE_JUNIT_XML_NAME: Final[str] = "TEST-CukesRunner.xml"
"""File name of the JUnit-XML report written inside the Surefire directory."""

TARGET_SUBDIR_NAMES: Final[tuple[str, ...]] = (
    PRETTY_REPORTS_DIR_NAME,
    SCREENSHOTS_DIR_NAME,
    ERROR_SHOTS_DIR_NAME,
    SUREFIRE_REPORTS_DIR_NAME,
)
"""The four directories created directly beneath :data:`TARGET_DIR_NAME`.

Together with the root itself this is the five-directory set that
:func:`ensure_target_layout` creates and that the ``Makefile`` ``dirs`` target
mirrors. It is a cross-module contract: do not add or drop an entry.
"""


# =============================================================================
# The layout itself.
# =============================================================================


class TargetLayout(NamedTuple):
    """An immutable, fully resolved view of one ``target/`` artifact tree.

    A layout is always rooted at a directory literally named ``target``. The
    optional *base directory* accepted by :meth:`for_base` shifts where that
    root sits -- which lets a test point the whole layout at a ``tmp_path``
    without polluting the working tree -- but it can never rename the root
    itself.

    The default layout (:data:`DEFAULT_LAYOUT`) is deliberately *relative*, so
    it renders as ``target/cucumber.json`` and matches, character for character,
    the paths the test configuration and the CI pipeline already use.
    """

    root: Path
    """The artifact root: ``target``."""

    cucumber_json: Path
    """``target/cucumber.json`` [README.md:L80]."""

    cucumber_html: Path
    """``target/cucumber-reports.html`` [README.md:L79]."""

    rerun_txt: Path
    """``target/rerun.txt`` [README.md:L81]."""

    pretty_reports_dir: Path
    """``target/cucumber`` [README.md:L82]."""

    screenshots_dir: Path
    """``target/screenshots`` [README.md:L42-L43]."""

    error_shots_dir: Path
    """``target/error-shots`` [README.md:L42-L43]."""

    surefire_reports_dir: Path
    """``target/surefire-reports`` -- name retained for consumer parity."""

    surefire_junit_xml: Path
    """``target/surefire-reports/TEST-CukesRunner.xml`` [pom.xml:L27]."""

    @classmethod
    def for_base(cls, base_dir: StrPath | None = None) -> Self:
        """Build the layout for *base_dir*.

        Args:
            base_dir: Directory the artifact root should sit inside. ``None``
                -- the default -- yields a root relative to the process working
                directory, exactly like the ``Makefile`` and the test
                configuration express it. The path is used as given: it is
                neither resolved nor expanded, because resolving would turn the
                relative default into an absolute path and expansion would read
                the environment, which this module never does.

        Returns:
            A new :class:`TargetLayout`. The root is always named ``target``.
        """
        root = Path(TARGET_DIR_NAME) if base_dir is None else Path(base_dir) / TARGET_DIR_NAME
        surefire_reports_dir = root / SUREFIRE_REPORTS_DIR_NAME
        return cls(
            root=root,
            cucumber_json=root / CUCUMBER_JSON_NAME,
            cucumber_html=root / CUCUMBER_HTML_NAME,
            rerun_txt=root / RERUN_TXT_NAME,
            pretty_reports_dir=root / PRETTY_REPORTS_DIR_NAME,
            screenshots_dir=root / SCREENSHOTS_DIR_NAME,
            error_shots_dir=root / ERROR_SHOTS_DIR_NAME,
            surefire_reports_dir=surefire_reports_dir,
            surefire_junit_xml=surefire_reports_dir / SUREFIRE_JUNIT_XML_NAME,
        )

    @property
    def subdirectories(self) -> tuple[Path, ...]:
        """The four directories directly beneath :attr:`root`, in name order."""
        return tuple(self.root / name for name in TARGET_SUBDIR_NAMES)

    @property
    def directories(self) -> tuple[Path, ...]:
        """Every directory :func:`ensure_target_layout` creates, root first.

        Exactly five entries: ``target/``, ``target/cucumber/``,
        ``target/screenshots/``, ``target/error-shots/`` and
        ``target/surefire-reports/``. The root comes first so that a caller
        walking the tuple never asks for a child before its parent.
        """
        return (self.root, *self.subdirectories)

    @property
    def report_files(self) -> tuple[Path, ...]:
        """The three report *files*, in the plugin order of [README.md:L79-L81].

        The PrettyReports output is excluded because it is a directory, not a
        file; it is available as :attr:`pretty_reports_dir`.
        """
        return (self.cucumber_html, self.cucumber_json, self.rerun_txt)

    def as_dict(self) -> dict[str, Path]:
        """Return the layout keyed by stable, configuration-friendly names.

        The keys are the lower-cased forms of the environment-variable and
        ``configuration.properties`` keys the committed templates already
        document (``TARGET_DIR`` -> ``target_dir``, ``CUCUMBER_JSON_PATH`` ->
        ``cucumber_json_path``, and so on), so a configuration layer or a
        configuration-introspection endpoint can map one onto the other without
        a translation table.

        A fresh dictionary is built on every call: the returned mapping is the
        caller's to mutate and can never corrupt module state.
        """
        return {
            "target_dir": self.root,
            "cucumber_json_path": self.cucumber_json,
            "cucumber_html_path": self.cucumber_html,
            "rerun_txt_path": self.rerun_txt,
            "pretty_reports_dir": self.pretty_reports_dir,
            "screenshots_dir": self.screenshots_dir,
            "error_shots_dir": self.error_shots_dir,
            "surefire_reports_dir": self.surefire_reports_dir,
            "surefire_junit_xml_path": self.surefire_junit_xml,
        }


# =============================================================================
# Repository-relative constants.
#
# These are the names every other module imports, and they are derived from a
# single layout instance so that a path can never be spelled two different ways.
# They are relative on purpose: `target/cucumber.json` is exactly how the test
# configuration, the CI publisher and the committed configuration templates
# already spell it.
#
# This module is the ONLY place in the application package where the literal
# artifact-root name is defined; every sibling imports from here rather than
# hard-coding a path.
# =============================================================================

DEFAULT_LAYOUT: Final[TargetLayout] = TargetLayout.for_base()
"""The layout used when no explicit base directory is supplied."""

TARGET_DIR: Final[Path] = DEFAULT_LAYOUT.root
"""``target`` -- the ephemeral artifact root, wiped by the ``clean`` target."""

CUCUMBER_HTML_PATH: Final[Path] = DEFAULT_LAYOUT.cucumber_html
"""``target/cucumber-reports.html`` [README.md:L79]."""

CUCUMBER_JSON_PATH: Final[Path] = DEFAULT_LAYOUT.cucumber_json
"""``target/cucumber.json`` [README.md:L80] -- the publisher's glob target."""

RERUN_TXT_PATH: Final[Path] = DEFAULT_LAYOUT.rerun_txt
"""``target/rerun.txt`` [README.md:L81]."""

PRETTY_REPORTS_DIR: Final[Path] = DEFAULT_LAYOUT.pretty_reports_dir
"""``target/cucumber`` [README.md:L82] -- the PrettyReports output directory."""

SCREENSHOTS_DIR: Final[Path] = DEFAULT_LAYOUT.screenshots_dir
"""``target/screenshots`` [README.md:L42-L43]."""

ERROR_SHOTS_DIR: Final[Path] = DEFAULT_LAYOUT.error_shots_dir
"""``target/error-shots`` [README.md:L42-L43]."""

SUREFIRE_REPORTS_DIR: Final[Path] = DEFAULT_LAYOUT.surefire_reports_dir
"""``target/surefire-reports`` -- name retained for report-consumer parity."""

SUREFIRE_JUNIT_XML_PATH: Final[Path] = DEFAULT_LAYOUT.surefire_junit_xml
"""``target/surefire-reports/TEST-CukesRunner.xml`` [pom.xml:L27]."""

TARGET_SUBDIRS: Final[tuple[Path, ...]] = DEFAULT_LAYOUT.subdirectories
"""The four directories beneath :data:`TARGET_DIR`."""

MANAGED_DIRECTORIES: Final[tuple[Path, ...]] = DEFAULT_LAYOUT.directories
"""The five directories :func:`ensure_target_layout` creates, root first."""

REPORT_FILES: Final[tuple[Path, ...]] = DEFAULT_LAYOUT.report_files
"""The three report files, in the plugin order of [README.md:L79-L81]."""


# =============================================================================
# Behaviour.
# =============================================================================


def target_root(base_dir: StrPath | None = None) -> Path:
    """Return the artifact root for *base_dir*.

    Args:
        base_dir: Optional directory the root should sit inside. ``None`` yields
            the repository-relative :data:`TARGET_DIR`.

    Returns:
        The root directory, always named ``target``.
    """
    return TargetLayout.for_base(base_dir).root


def resolve_layout(base_dir: StrPath | None = None) -> TargetLayout:
    """Return the full :class:`TargetLayout` for *base_dir*.

    A thin, discoverable alias for :meth:`TargetLayout.for_base` so callers do
    not have to import the class to obtain a layout.

    Args:
        base_dir: Optional directory the artifact root should sit inside.

    Returns:
        The layout rooted at ``<base_dir>/target``, or at ``target`` when
        *base_dir* is ``None``.
    """
    return TargetLayout.for_base(base_dir)


def managed_directories(base_dir: StrPath | None = None) -> tuple[Path, ...]:
    """Return the five directories this module manages, root first.

    Args:
        base_dir: Optional directory the artifact root should sit inside.

    Returns:
        An immutable tuple of ``target/``, ``target/cucumber/``,
        ``target/screenshots/``, ``target/error-shots/`` and
        ``target/surefire-reports/``. Compare it with the result of
        :func:`ensure_target_layout` to detect a partial failure.
    """
    return TargetLayout.for_base(base_dir).directories


def artifact_paths(base_dir: StrPath | None = None) -> dict[str, Path]:
    """Return every artifact path keyed by its configuration name.

    Args:
        base_dir: Optional directory the artifact root should sit inside.

    Returns:
        A freshly built mapping -- see :meth:`TargetLayout.as_dict` for the key
        names, which mirror the committed configuration templates. Pair it with
        :func:`to_posix` when string values are needed::

            {key: to_posix(value) for key, value in artifact_paths().items()}
    """
    return TargetLayout.for_base(base_dir).as_dict()


def to_posix(path: StrPath) -> str:
    """Render *path* with forward slashes, on every platform.

    Report consumers -- and above all the CI publisher's
    ``fileIncludePattern: '**/*.json'`` glob [Jenkins:L15] -- are written in
    terms of forward slashes. Emitting a Windows-flavoured
    ``target\\cucumber.json`` into a report, a JSON response or a log line
    would invalidate that glob, so any string form of a path leaving this
    application should be produced here.

    Args:
        path: The path to render.

    Returns:
        The POSIX-style string form of *path*.
    """
    return Path(path).as_posix()


def ensure_target_layout(base_dir: StrPath | None = None) -> tuple[Path, ...]:
    """Create the ``target/`` tree, and never raise while doing so.

    This is the function that closes the port's most consequential silent
    failure: the Cucumber-JSON writer does not create the parent directory of
    the file it writes, so a run begun while ``target/`` is absent dies at
    session finish with ``FileNotFoundError``. Maven created the directory
    implicitly; here it is created explicitly, up front.

    Two of the other report writers configured for the same run -- the JUnit-XML
    and HTML ones -- *do* create their own parent directories, so they can
    incidentally mask that failure. Leaning on that would make artifact
    production depend on which end-of-session hook happens to run first, and on
    those two writers continuing to point inside ``target/``. The tree is
    therefore created unconditionally and in advance instead.

    Two guarantees have to hold at once, and they pull in opposite directions:

    * The directories must really be created in any writable environment -- a
      run started from a clean checkout has to end with ``target/cucumber.json``
      on disk.
    * The call must never abort the caller. The application factory invokes it
      during start-up, so raising here would make importing the WSGI entrypoint
      fail outright on a read-only or otherwise restricted filesystem.

    Both are satisfied by always attempting creation and degrading to a single
    warning if the filesystem refuses.

    Creation uses ``mkdir(parents=True, exist_ok=True)``, which makes the call
    both idempotent and safe against races: the test suite runs under
    ``pytest-xdist`` with one worker per logical CPU, so several processes may
    reach this function at the same instant and try to create the same
    directories. A hand-rolled "check, then create" would be exactly the race
    that ``exist_ok=True`` removes.

    Nothing is ever deleted here. Wiping the tree belongs to the ``clean``
    target that ports ``mvn clean``.

    Args:
        base_dir: Optional directory the artifact root should sit inside.
            ``None`` -- the default -- creates ``target/`` relative to the
            process working directory. Tests pass a temporary directory so the
            real tree is left untouched.

    Returns:
        The directories that exist as a result of this call, root first, in the
        order of :attr:`TargetLayout.directories`. A full success returns all
        five; an empty tuple means nothing could be created. Compare the length
        with :func:`managed_directories` to detect a partial failure -- the
        result is never ``None``, so a caller always has a signal to act on.
    """
    layout = TargetLayout.for_base(base_dir)
    ensured: list[Path] = []
    failures: list[tuple[Path, OSError]] = []

    for directory in layout.directories:
        try:
            # parents=True: create intermediate directories, so a caller-supplied
            #               base directory need not exist beforehand.
            # exist_ok=True: idempotent, and race-safe across xdist workers.
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            # OSError covers the whole family that matters here: PermissionError,
            # read-only filesystems, a non-directory in the way, and ENOSPC.
            failures.append((directory, error))
            continue
        ensured.append(directory)

    if failures:
        first_directory, first_error = failures[0]
        _LOGGER.warning(
            "Could not create %d of %d artifact directories under %s; continuing "
            "without them, so report artifacts written there may fail. "
            "First failure: %s (%s: %s)",
            len(failures),
            len(layout.directories),
            to_posix(layout.root),
            to_posix(first_directory),
            type(first_error).__name__,
            first_error,
        )
    else:
        _LOGGER.debug(
            "Ensured %d artifact directories under %s",
            len(ensured),
            to_posix(layout.root),
        )

    return tuple(ensured)


# The application factory, the Makefile-driven test run and the BDD conftest all
# reach for this behaviour by name. These aliases exist so that every reasonable
# spelling resolves to the one implementation above; they are the same object,
# not copies, so behaviour can never drift between them.
ensure_target_dirs = ensure_target_layout
ensure_target_directories = ensure_target_layout
ensure_layout = ensure_target_layout


def ensure_parent_directory(path: StrPath) -> bool:
    """Create the parent directory of *path*, and never raise while doing so.

    Report writers are handed a *file* path -- ``target/rerun.txt``,
    ``target/surefire-reports/TEST-CukesRunner.xml`` -- and need its directory to
    exist before they open it. Routing that through this module keeps the
    ``mkdir`` calls, and therefore the layout knowledge, in one place instead of
    scattering them across the reporting adapters.

    Args:
        path: A file path whose parent directory should exist. A bare file name
            has ``.`` as its parent, which trivially already exists.

    Returns:
        ``True`` if the parent directory exists once the call returns, ``False``
        if the filesystem refused -- in which case a warning has been logged and
        no exception is raised, matching :func:`ensure_target_layout`.
    """
    parent = Path(path).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        _LOGGER.warning(
            "Could not create parent directory %s for %s (%s: %s)",
            to_posix(parent),
            to_posix(path),
            type(error).__name__,
            error,
        )
        return False
    return True
