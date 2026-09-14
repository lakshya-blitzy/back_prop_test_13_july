"""Single source of truth for every filesystem path used by the Testinium-QA port.

Path drift is how this port breaks silently, so ownership is explicit
(AAP 0.4.2): *"Artifact paths are owned by ``app/utils/paths.py``. Every writer,
the artifact route, the clean step and the per-worker invocation take their
paths from it, and no other Python module contains a path literal."*  Anything
under ``app/``, ``features/``, ``scripts/`` or ``tests/`` that needs a path asks
this module for it instead of spelling one out.

The four report artifacts
-------------------------
The artifact paths are the ones the Java runner declared in its Cucumber plugin
list.  They are reproduced verbatim inside this repository at
``README.md:79-82``, which quotes the original ``CukesRunner.java:9-14``::

    "html:target/cucumber-reports.html",
    "json:target/cucumber.json",
    "rerun:target/rerun.txt",
    "me.jvt.cucumber.report.PrettyReports:target/cucumber"

The first three are files; the fourth is a directory whose pages and assets sit
one level deeper, under ``target/cucumber/cucumber-html-reports/``, with
``overview-features.html`` as the overview index (AAP 0.3.4).
``target/cucumber.json`` is also the exact value of the Jenkins publisher's
``fileIncludePattern`` (``Jenkins:15``), which is why the relative-path
constants render with forward slashes on every platform.

Workers
-------
``pom.xml:22-23`` configures surefire with ``parallel=methods`` and
``useUnlimitedThreads``; the port reproduces that intent with a process pool, so
per-worker intermediate results live under ``target/.workers/`` and are named
uniquely by *both* process id and shard index (see :func:`worker_result_path`).
That directory sits beneath ``target/`` - which is what makes the narrowed
Jenkins glob necessary - yet must never be reachable through the HTTP artifact
route, which is why :func:`resolve_artifact` rejects every dot-prefixed path
component.

Conventions
-----------
* Every working-directory-relative accessor takes ``base`` as its last
  parameter and defaults to :meth:`pathlib.Path.cwd`, matching how Maven
  resolved ``target/`` against the working directory and how the Java
  ``ConfigurationReader`` opened a bare relative filename.  The current
  directory is read on every call and never cached at import time, and the
  module holds no mutable state, so the accessors are safe under a process
  pool.  ``base`` is the only override mechanism - no environment layer is
  added (AAP 0.4.1) - and it exists so tests can inject a temporary directory.
* The four package-relative locators (:func:`package_root`,
  :func:`templates_dir`, :func:`static_dir`, :func:`vendor_dir`) take no
  ``base``: they are derived from ``__file__`` and are therefore unaffected by
  the working directory of whichever process asks.
* Only the Python standard library is imported.  ``app/utils`` imports nothing
  from the ``app`` package (AAP 0.4.2), so this module is importable in a
  worker that never builds a Flask application.
* **This module creates directories and opens files, and never deletes
  anything.**  There is no ``rmtree``, ``unlink``, ``rmdir`` or "clean" helper
  here, by design: AAP 0.4.1 assigns emptying ``target/`` (the ``--clean``
  step) and tearing down ``target/.workers/`` to ``app/cli.py``.  This module's
  job is to hand those callers the path.  Do not add a delete helper here - not
  even as part of a write: the no-follow helpers below open through a held
  directory descriptor precisely so that they need no temporary file and no
  replacement of the destination, which would require exactly the deletion
  this module does not do.

Artifact I/O: no-follow helpers and the trusted anchor
------------------------------------------------------
``target/`` is generated output that outlives a run - ``run-tests --no-clean``
keeps it, and a clean that failed part-way leaves some of it - so a symbolic
link can be sitting at ``target/``, at ``target/cucumber/`` or at
``target/cucumber.json`` by the time a writer opens its output, and
``GET /artifacts/<path:name>`` turns request input into a filesystem read.
Validating a *pathname* and then opening it leaves a window between the two in
which the name can come to mean a different object (CWE-367), so the four
writers and the artifact route do not open artifact paths themselves.  They
take an already-verified object from here:

* :func:`ensure_dir` and :func:`ensure_parent` create and verify every
  directory component under a held directory descriptor, and
  :func:`ensure_parent` also refuses a final entry that already exists as a
  symbolic link - the check that protects a writer which then opens the
  returned path itself.
* :func:`open_artifact_write` returns a stream for an artifact write,
  :func:`open_artifact_read` and :func:`read_artifact_text` return an
  already-verified regular file for an artifact read, and
  :func:`open_resolved_artifact` validates an untrusted request name and opens
  the file it validated in the same operation, for the artifact route.
* :exc:`ArtifactPathError` is what a refused component raises.  It subclasses
  :exc:`OSError`, so a writer's existing ``except OSError`` maps it to the
  writer-failure exit class of AAP 0.4.1 with no change and no new exit class.

**The trusted anchor.**  Verification runs from the ``target`` component
inward, and no further out.  Everything above the artifact root is the
operator's own filesystem layout - on macOS both ``/tmp`` and ``/var`` are
symbolic links, and a temporary directory a test injects through ``base`` sits
under them - so those components are followed normally.  Concretely: a path is
split at its **last** component equal to ``target``; the prefix is the trusted
anchor and is opened following symbolic links, while ``target`` itself and
every component below it is created and opened with ``O_NOFOLLOW`` under the
descriptor of its already-verified parent.  A path with no ``target`` component
at all - ``write_pretty_reports`` accepts an arbitrary output directory, and
tests pass one - is anchored at its own parent, and only its final component is
checked.

**What the checks on the opened object add.**  ``O_NOFOLLOW`` refuses a
*symbolic* link and nothing else, so every artifact this module opens - for
reading or for writing, on both branches below - is also :func:`os.fstat`-ed
through its own descriptor and required to be a **regular file with exactly one
link**.  The link count is the part that is easy to miss: an entry hard-linked
to a file outside the artifact root *is* that file, with no symlink anywhere in
the path, so writing the artifact would overwrite it and serving the artifact
would disclose it.  A writer creates its own output, so one link is what a
genuine artifact has.  For the same reason a write is opened **without**
``O_TRUNC`` and truncated only once those checks have passed: truncating during
the open would destroy the wrong file before anything could refuse it.

**Portability.**  ``O_NOFOLLOW`` and the ``dir_fd`` parameter are POSIX-only,
while AAP 0.8 requires Windows, Linux and macOS.  Availability is therefore
detected rather than assumed.  Where the primitives are missing the helpers
refuse a symlinked component found by :meth:`~pathlib.Path.is_symlink`, open the
path ordinarily, and then **confirm that the object they hold open is the one
the name refers to**, by comparing device and inode with a no-follow
:func:`os.lstat` of the same name (:func:`_verify_opened_by_name`).  So the
fallback is not a check-then-open race either: something swapped in between the
lexical check and the open fails that comparison and is refused - before any
truncation, since the write path truncates last.  What the fallback cannot do is
*prevent* the open, only establish afterwards that it landed on the wrong
object; with the primitives present, ``O_NOFOLLOW`` under a held directory
descriptor prevents it outright.
"""

import errno
import io
import os
import stat
from pathlib import Path
from typing import IO, Any, BinaryIO, Final, NamedTuple

__all__ = [
    "ARTIFACT_SPECS",
    "CUCUMBER_JSON_NAME",
    "CUCUMBER_JSON_RELPATH",
    "CUCUMBER_REPORTS_HTML_NAME",
    "CUCUMBER_REPORTS_HTML_RELPATH",
    "FEATURES_DIR_NAME",
    "FILE_URI_SCHEME",
    "LEGACY_FEATURES_PREFIX",
    "NORMALIZED_FEATURES_PREFIX",
    "PRETTY_HTML_SUBDIR",
    "PRETTY_OVERVIEW_INDEX",
    "PRETTY_REPORTS_DIR_NAME",
    "PRETTY_REPORTS_RELPATH",
    "RERUN_TXT_NAME",
    "RERUN_TXT_RELPATH",
    "TARGET_DIR_NAME",
    "WORKERS_DIR_NAME",
    "ArtifactPathError",
    "ArtifactSpec",
    "artifact_path",
    "cucumber_json_path",
    "cucumber_reports_html_path",
    "ensure_dir",
    "ensure_parent",
    "features_dir",
    "iter_worker_result_paths",
    "normalize_feature_uri",
    "open_artifact_read",
    "open_artifact_write",
    "open_resolved_artifact",
    "package_root",
    "pretty_reports_dir",
    "pretty_reports_html_dir",
    "pretty_reports_index_path",
    "read_artifact_text",
    "rerun_txt_path",
    "resolve_artifact",
    "static_dir",
    "target_root",
    "templates_dir",
    "vendor_dir",
    "worker_result_path",
    "workers_dir",
]

# --------------------------------------------------------------------------- #
# Directory and file name components
# --------------------------------------------------------------------------- #

#: Build output directory.  ``mvn clean test`` emptied it; ``run-tests
#: --clean`` empties it now (AAP 0.4.1).
TARGET_DIR_NAME: Final[str] = "target"

#: Gherkin feature directory at the repository root.  The Java runner declared
#: ``features = "src/main/resources/features"`` (``README.md:84``); AAP
#: deviation 1 moves the features to ``features/`` while preserving filenames,
#: because the filenames appear in the JSON ``uri``, in the rerun manifest and
#: in user commands.
FEATURES_DIR_NAME: Final[str] = "features"

#: Per-worker intermediate results directory, beneath ``target/``.  Dot-
#: prefixed so it is trivially distinguishable from published artifacts, and
#: unreachable through the HTTP artifact route (AAP 0.3.1).
WORKERS_DIR_NAME: Final[str] = ".workers"

#: Output directory of the PrettyReports generator (``README.md:82``).
PRETTY_REPORTS_DIR_NAME: Final[str] = "cucumber"

#: Sub-directory the PrettyReports generator writes its pages and assets into
#: (AAP 0.3.4).
PRETTY_HTML_SUBDIR: Final[str] = "cucumber-html-reports"

#: The PrettyReports overview page, served when a request names the
#: ``cucumber/`` directory itself (AAP 0.3.1).
PRETTY_OVERVIEW_INDEX: Final[str] = "overview-features.html"

#: File name of the single self-contained HTML report (``README.md:79``).
CUCUMBER_REPORTS_HTML_NAME: Final[str] = "cucumber-reports.html"

#: File name of the Cucumber-JVM JSON report (``README.md:80``).  The only
#: artifact with a machine consumer in CI - the Jenkins publisher reads it and
#: nothing else (``Jenkins:15``).
CUCUMBER_JSON_NAME: Final[str] = "cucumber.json"

#: File name of the rerun manifest (``README.md:81``), read back by
#: ``run-tests --rerun``.
RERUN_TXT_NAME: Final[str] = "rerun.txt"

# --------------------------------------------------------------------------- #
# POSIX-style relative-path strings
#
# These are strings rather than Path objects on purpose: they are quoted in
# README.md, listed by the ``GET /`` route and must agree byte-for-byte with the
# Jenkins publisher's ``fileIncludePattern``.  They are joined with an explicit
# "/" so they render identically on POSIX and Windows - a backslash here would
# break both the README and the publisher glob.
# --------------------------------------------------------------------------- #

#: ``target/cucumber-reports.html`` (``README.md:79``).
CUCUMBER_REPORTS_HTML_RELPATH: Final[str] = (
    f"{TARGET_DIR_NAME}/{CUCUMBER_REPORTS_HTML_NAME}"
)

#: ``target/cucumber.json`` (``README.md:80``).  Equal to the Jenkins
#: publisher's narrowed ``fileIncludePattern`` (``Jenkins:15``).
CUCUMBER_JSON_RELPATH: Final[str] = f"{TARGET_DIR_NAME}/{CUCUMBER_JSON_NAME}"

#: ``target/rerun.txt`` (``README.md:81``).
RERUN_TXT_RELPATH: Final[str] = f"{TARGET_DIR_NAME}/{RERUN_TXT_NAME}"

#: ``target/cucumber`` (``README.md:82``) - a directory, not a file.
PRETTY_REPORTS_RELPATH: Final[str] = f"{TARGET_DIR_NAME}/{PRETTY_REPORTS_DIR_NAME}"

#: Scheme prefix every feature URI carries in the JSON report and in the rerun
#: manifest, e.g. ``"file:features/Crm.feature"`` (AAP 0.6).
FILE_URI_SCHEME: Final[str] = "file:"

#: Feature-directory prefix used by the Java implementation, kept verbatim in
#: the golden fixtures.  The trailing slash is deliberate, so removing it
#: yields a bare filename.
LEGACY_FEATURES_PREFIX: Final[str] = "src/main/resources/features/"

#: Feature-directory prefix this port emits (AAP deviation 1).
NORMALIZED_FEATURES_PREFIX: Final[str] = f"{FEATURES_DIR_NAME}/"


class ArtifactSpec(NamedTuple):
    """One of the four report artifacts declared in the Cucumber plugin list.

    Attributes:
        key: Short, stable identifier.  The keys double as the allowlisted
            names of the ``GET /artifacts/<path:name>`` route (AAP 0.3.1), so
            they are exactly the final path components of the four plugin
            targets.
        relpath: POSIX-style path relative to the repository root.
        is_dir: ``True`` only for the PrettyReports tree; the other three
            artifacts are single files.
    """

    key: str
    relpath: str
    is_dir: bool


#: The four artifacts, in the plugin order of ``README.md:79-82`` (html, json,
#: rerun, pretty).  The ``GET /`` route lists them in this order.
ARTIFACT_SPECS: Final[tuple[ArtifactSpec, ...]] = (
    ArtifactSpec(
        key=CUCUMBER_REPORTS_HTML_NAME,
        relpath=CUCUMBER_REPORTS_HTML_RELPATH,
        is_dir=False,
    ),
    ArtifactSpec(
        key=CUCUMBER_JSON_NAME,
        relpath=CUCUMBER_JSON_RELPATH,
        is_dir=False,
    ),
    ArtifactSpec(
        key=RERUN_TXT_NAME,
        relpath=RERUN_TXT_RELPATH,
        is_dir=False,
    ),
    ArtifactSpec(
        key=PRETTY_REPORTS_DIR_NAME,
        relpath=PRETTY_REPORTS_RELPATH,
        is_dir=True,
    ),
)

_SPECS_BY_KEY: Final[dict[str, ArtifactSpec]] = {
    spec.key: spec for spec in ARTIFACT_SPECS
}

#: The three artifact keys a request may name directly.  The fourth key,
#: ``cucumber``, is a directory and is handled separately by
#: :func:`resolve_artifact`.
_ALLOWED_FILE_KEYS: Final[frozenset[str]] = frozenset(
    spec.key for spec in ARTIFACT_SPECS if not spec.is_dir
)

#: Name components of a per-worker result file.  Both the process id and the
#: shard index take part, because ``pom.xml:22-23`` implies a pool in which one
#: process may handle several shards (a pid alone would collide) and several
#: processes handle the same shard indices (an index alone would collide).
_WORKER_RESULT_PREFIX: Final[str] = "worker-"
_WORKER_RESULT_SUFFIX: Final[str] = ".json"
_WORKER_RESULT_TEMPLATE: Final[str] = (
    _WORKER_RESULT_PREFIX + "{pid}-{shard_index:04d}" + _WORKER_RESULT_SUFFIX
)


def _base(base: Path | str | None) -> Path:
    """Resolve the ``base`` argument shared by every path accessor.

    Args:
        base: Directory the relative artifact paths hang off, or ``None`` for
            the process working directory.

    Returns:
        ``Path(base)`` when a base was supplied, otherwise the current working
        directory, read fresh on every call so a worker process - or a test -
        that changes directory sees the change.
    """
    return Path(base) if base is not None else Path.cwd()


# --------------------------------------------------------------------------- #
# Working-directory-relative accessors.  None of these touches the filesystem.
# --------------------------------------------------------------------------- #


def target_root(base: Path | str | None = None) -> Path:
    """Return the build output directory, ``<base>/target``.

    This is the directory ``run-tests --clean`` empties (AAP 0.4.1) and the
    containment root :func:`resolve_artifact` validates against.

    Args:
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The ``target`` directory path.  No filesystem access is performed and
        the directory is not created.
    """
    return _base(base) / TARGET_DIR_NAME


def features_dir(base: Path | str | None = None) -> Path:
    """Return the Gherkin feature directory, ``<base>/features``.

    Consumed by ``app/services/test_run_service.py``, which shards a run by
    parsing the feature files and applying the tag expression (AAP 0.6), and by
    the engine, which discovers ``features/environment.py`` and
    ``features/steps/`` relative to it.

    Args:
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The ``features`` directory path.
    """
    return _base(base) / FEATURES_DIR_NAME


def cucumber_reports_html_path(base: Path | str | None = None) -> Path:
    """Return ``<base>/target/cucumber-reports.html`` (``README.md:79``).

    The single self-contained HTML page written by
    ``app/reporting/html_report.py``.

    Args:
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The HTML report path.
    """
    return target_root(base) / CUCUMBER_REPORTS_HTML_NAME


def cucumber_json_path(base: Path | str | None = None) -> Path:
    """Return ``<base>/target/cucumber.json`` (``README.md:80``).

    Written by ``app/reporting/cucumber_json.py`` and read by the Jenkins
    Cucumber publisher, by the report routes and by ``app/web/routes.py``.

    Args:
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The JSON report path.
    """
    return target_root(base) / CUCUMBER_JSON_NAME


def rerun_txt_path(base: Path | str | None = None) -> Path:
    """Return ``<base>/target/rerun.txt`` (``README.md:81``).

    Written by ``app/reporting/rerun_report.py`` and read back by
    ``run-tests --rerun``, which is the port of ``FailedTestRunner``'s
    ``features = "@target/rerun.txt"``.

    Args:
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The rerun manifest path.
    """
    return target_root(base) / RERUN_TXT_NAME


def pretty_reports_dir(base: Path | str | None = None) -> Path:
    """Return ``<base>/target/cucumber`` (``README.md:82``).

    The root of the PrettyReports tree written by
    ``app/reporting/pretty_reports.py``.  This is the only one of the four
    artifacts that is a directory.

    Args:
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The PrettyReports output directory path.
    """
    return target_root(base) / PRETTY_REPORTS_DIR_NAME


def pretty_reports_html_dir(base: Path | str | None = None) -> Path:
    """Return ``<base>/target/cucumber/cucumber-html-reports``.

    The generator writes its overview pages, detail pages and vendored assets
    one level below the plugin's output directory (AAP 0.3.4).

    Args:
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The directory holding the PrettyReports pages and assets.
    """
    return pretty_reports_dir(base) / PRETTY_HTML_SUBDIR


def pretty_reports_index_path(base: Path | str | None = None) -> Path:
    """Return the PrettyReports overview page.

    ``<base>/target/cucumber/cucumber-html-reports/overview-features.html`` is
    what a request naming the ``cucumber/`` directory is served (AAP 0.3.1).

    Args:
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The PrettyReports overview page path.
    """
    return pretty_reports_html_dir(base) / PRETTY_OVERVIEW_INDEX


def artifact_path(key: str | ArtifactSpec, base: Path | str | None = None) -> Path:
    """Resolve one of the four artifacts by key, or by spec.

    Used by the ``GET /`` route, which iterates :data:`ARTIFACT_SPECS` to report
    the presence and modification time of each artifact.

    A supplied :class:`ArtifactSpec` is **not** trusted to describe its own
    location.  :class:`ArtifactSpec` is a plain :class:`~typing.NamedTuple`, so
    any caller can build one, and a hand-built ``ArtifactSpec("x",
    "../../secret", False)`` would otherwise hand back a path outside
    ``target/`` and outside ``base``.  A spec argument is therefore looked up
    by its own ``key`` and required to be *equal* to the canonical member of
    :data:`ARTIFACT_SPECS` - all three fields - so the returned path is built
    from this module's own constants in every case.  The result is then checked
    to lie inside :func:`target_root`, which holds for all four artifacts
    because every canonical ``relpath`` starts with ``target/``.

    Args:
        key: An :class:`ArtifactSpec`, or one of its ``key`` values -
            ``"cucumber-reports.html"``, ``"cucumber.json"``, ``"rerun.txt"`` or
            ``"cucumber"``.
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The artifact path.  For the ``"cucumber"`` key this is the
        PrettyReports *directory*, matching :attr:`ArtifactSpec.is_dir`.  Still
        no filesystem access: the containment check is lexical.

    Raises:
        KeyError: If ``key`` is not one of the four artifact keys, or is an
            :class:`ArtifactSpec` that is not one of the four canonical specs.
            Unlike :func:`resolve_artifact`, which validates untrusted request
            input and returns ``None``, this function is called with a key
            chosen in code, so either is a programming error and is raised
            loudly.  The message names the four canonical keys and never echoes
            a caller-supplied ``relpath``, which would reflect attacker-chosen
            text into a log line for no diagnostic gain.
    """
    if isinstance(key, ArtifactSpec):
        try:
            canonical = _SPECS_BY_KEY.get(key.key)
        except TypeError:
            # A hand-built spec can carry an unhashable value in ``key``; that
            # is the same programming error as an unknown key, not a TypeError
            # for the caller to discover separately.
            canonical = None
        if canonical is None or canonical != key:
            raise KeyError(
                "unknown artifact spec; expected one of the four members of "
                f"ARTIFACT_SPECS, keyed {tuple(_SPECS_BY_KEY)}"
            )
        spec = canonical
    else:
        try:
            spec = _SPECS_BY_KEY[key]
        except KeyError:
            raise KeyError(
                f"unknown artifact key {key!r}; expected one of {tuple(_SPECS_BY_KEY)}"
            ) from None
    path = _base(base).joinpath(*spec.relpath.split("/"))
    if not path.is_relative_to(target_root(base)):
        # Unreachable while ARTIFACT_SPECS holds the four canonical relpaths,
        # and checked anyway: this function is the single source of the paths
        # every writer and the artifact route open (AAP 0.4.2), so its
        # containment invariant is asserted here rather than assumed by each
        # of them.
        raise KeyError(
            f"artifact {spec.key!r} does not resolve inside the artifact root; "
            "ARTIFACT_SPECS is inconsistent with TARGET_DIR_NAME"
        )
    return path


# --------------------------------------------------------------------------- #
# Per-worker intermediates
# --------------------------------------------------------------------------- #


def workers_dir(base: Path | str | None = None) -> Path:
    """Return ``<base>/target/.workers``, the per-worker intermediate directory.

    ``app/services/test_run_service.py`` invokes the engine once per worker with
    an output path in this directory, then merges the files it finds.
    ``app/cli.py`` creates it before the run and removes it before the command
    returns, so no intermediate JSON is ever visible to the Jenkins publisher
    (AAP 0.4.1).  The leading dot is what keeps it unreachable through the HTTP
    artifact route (AAP 0.3.1, and :func:`resolve_artifact`).

    Args:
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The intermediate results directory path.  It is not created here - see
        :func:`ensure_dir`.
    """
    return target_root(base) / WORKERS_DIR_NAME


def worker_result_path(
    shard_index: int,
    pid: int | None = None,
    base: Path | str | None = None,
) -> Path:
    """Return the intermediate result file for one worker and one shard.

    The name is unique by *both* the process id and the shard index, and both
    parts are load-bearing.  ``pom.xml:22-23`` configures surefire with
    ``parallel=methods`` and ``useUnlimitedThreads``, which AAP deviation 4
    reproduces as a process pool: a process id alone collides whenever one
    worker process handles more than one shard, and a shard index alone
    collides across processes.  Nothing else in the port derives this name.

    Args:
        shard_index: Zero-based index of the shard the worker is running.  It
            is zero-padded in the filename so lexicographic order - the order
            :func:`iter_worker_result_paths` returns - matches numeric order.
        pid: Process id to embed; defaults to :func:`os.getpid` of the calling
            process.  Passed explicitly by a parent that names a child's output
            file, and by tests.
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        A path of the form ``<base>/target/.workers/worker-<pid>-<shard>.json``.
        The file is not created and its parent directory is not created.
    """
    resolved_pid = os.getpid() if pid is None else pid
    filename = _WORKER_RESULT_TEMPLATE.format(pid=resolved_pid, shard_index=shard_index)
    return workers_dir(base) / filename


def iter_worker_result_paths(base: Path | str | None = None) -> tuple[Path, ...]:
    """Return the per-worker result files that currently exist, ordered by name.

    The merge step in ``app/services/test_run_service.py`` must not depend on
    filesystem iteration order, because AAP 0.6 requires the merged structure to
    be deterministic for a fixed set of shard inputs.

    Exactly one absence is tolerated, and nothing else: a worker directory that
    is not there, or is not a directory at all, genuinely means *no worker has
    written*, which is the state before the first shard finishes and after
    ``app/cli.py`` has torn the directory down.  Every other failure - a denied
    traversal, an I/O error, a stat that fails on one entry - is a real
    infrastructure failure, and reporting it as "no results" would let a merge
    publish an empty result set as if the run had produced nothing, hiding
    completed shards behind a success.  Those propagate, so that
    ``app/services/test_run_service.py`` can name the failure on stderr and
    apply its non-zero exit class (AAP 0.4.1) instead.

    Args:
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        A tuple of the worker result files, sorted by filename - which, thanks
        to the zero-padded shard index in :func:`worker_result_path`, is also
        numeric order within a process id.  An empty tuple is returned when the
        worker directory is absent or is not a directory, and when it is
        readable but holds no matching regular file.  An entry that disappears
        mid-scan, or whose symlink target is gone, is skipped the same way -
        it is an absence, not a failure.

    Raises:
        OSError: Every other failure, unchanged and undisguised:
            :exc:`PermissionError` on the directory or on one entry, an I/O
            error while listing, a symlink loop on an entry.  Only
            :exc:`FileNotFoundError` and :exc:`NotADirectoryError` are read as
            absence, and the per-entry test is made separately from the listing
            so that one unreadable entry can never be mistaken for an empty
            directory.
    """
    directory = workers_dir(base)
    try:
        # iterdir() is lazy, so the listing is materialised inside the guard:
        # a failure part-way through the directory must be caught here rather
        # than surfacing later, where the tolerated-absence rule below would
        # not apply to it.
        entries = list(directory.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return ()

    selected: list[Path] = []
    for entry in entries:
        if not (
            entry.name.startswith(_WORKER_RESULT_PREFIX)
            and entry.name.endswith(_WORKER_RESULT_SUFFIX)
        ):
            continue
        try:
            # stat() rather than is_file(): on this interpreter is_file()
            # delegates to os.path.isfile(), which answers False for *every*
            # OSError, so a permission failure on one entry would be
            # indistinguishable from a directory entry. Symlinks are followed,
            # which is what is_file() did, so a linked result file still
            # counts.
            info = entry.stat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        if stat.S_ISREG(info.st_mode):
            selected.append(entry)
    return tuple(sorted(selected, key=lambda entry: entry.name))


# --------------------------------------------------------------------------- #
# No-follow artifact I/O.
#
# This module creates directories and opens files, and never deletes anything -
# see the module docstring, which also states the trusted-anchor rule and the
# portability fallback the private helpers below implement.
# --------------------------------------------------------------------------- #


class ArtifactPathError(OSError):
    """An artifact path component failed no-follow verification.

    Raised when a component of an artifact path - from the ``target`` component
    inward, per the trusted-anchor rule in the module docstring - is a symbolic
    link, is not a directory where the path requires one, or is not a regular
    file where the path requires one.

    It subclasses :exc:`OSError` deliberately.  Each of the four artifact
    writers already treats an :exc:`OSError` from its output path as a writer
    failure, which AAP 0.4.1 maps to a non-zero exit with the failing writer
    named on stderr and earlier artifacts left in place, so a refused symlink
    joins that class without any writer having to learn a new exception type
    and without a second exit class being invented for it.

    The message names the offending path component and nothing else.  In
    particular :func:`open_resolved_artifact` converts every rejection into
    ``None`` rather than surfacing this exception, so that ``app/errors.py``
    can answer one plain 404 that discloses no filesystem path and no attempted
    name.

    Examples:
        >>> isinstance(ArtifactPathError("refused"), OSError)
        True
    """


#: ``os.O_NOFOLLOW`` where the platform provides it, ``0`` where it does not.
#: Combined into an ``os.open`` flag set, ``0`` is a no-op, which is why the
#: fallback below has to make the check lexically instead.
_O_NOFOLLOW: Final[int] = getattr(os, "O_NOFOLLOW", 0)

#: ``os.O_DIRECTORY`` where the platform provides it, ``0`` where it does not.
_O_DIRECTORY: Final[int] = getattr(os, "O_DIRECTORY", 0)

#: ``os.O_NONBLOCK`` where the platform provides it, ``0`` where it does not.
#: Every artifact open carries it, and not for performance: opening a **FIFO**
#: blocks until the other end is opened, so an entry replaced by one would hang
#: a writer - or the artifact route - for ever, before the check that would
#: have refused it could run.  With this flag the open returns immediately and
#: :func:`_verify_open_artifact` refuses what it finds; POSIX gives the flag no
#: effect on a regular file, so a genuine artifact is unaffected.
_O_NONBLOCK: Final[int] = getattr(os, "O_NONBLOCK", 0)

#: Whether this platform can open a name *relative to a directory descriptor*
#: while refusing to follow a final symlink - the two primitives that close the
#: check-to-open window.  Both are POSIX-only, and AAP 0.8 requires Windows,
#: Linux and macOS, so the helpers degrade to a lexical check rather than
#: failing to import.  ``os.stat`` is included because the create path needs a
#: descriptor-relative ``lstat`` to tell an existing directory from a symlink.
_NO_FOLLOW_SUPPORTED: Final[bool] = bool(
    _O_NOFOLLOW
    and _O_DIRECTORY
    and os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
)

#: Components that cannot be verified as themselves: ``.`` names the directory
#: already held open and ``..`` names one above it, so neither can be checked
#: no-follow and neither ever appears in a path this module builds.
_UNVERIFIABLE_COMPONENTS: Final[frozenset[str]] = frozenset({os.curdir, os.pardir})


def _trusted_anchor(path: Path) -> tuple[Path, tuple[str, ...]]:
    """Split a path into its trusted anchor and the components to verify.

    Implements the trusted-anchor rule stated in the module docstring: the path
    is split at its **last** component equal to :data:`TARGET_DIR_NAME`, the
    prefix being the operator's own filesystem layout - followed normally - and
    ``target`` itself plus everything below it being the artifact tree this
    module owns and verifies no-follow.  A path with no ``target`` component is
    anchored at its own parent, so only its final component is checked; that is
    the case ``write_pretty_reports`` reaches when a caller supplies an
    arbitrary output directory, and the case a test reaches with a temporary
    directory.

    Args:
        path: The artifact path, absolute or relative.

    Returns:
        The anchor directory, and the components to verify beneath it in order.
        The components tuple is empty only for a path that names no entry at
        all - a bare filesystem root, or ``.``.
    """
    parts = path.parts
    for position in range(len(parts) - 1, -1, -1):
        if parts[position] == TARGET_DIR_NAME:
            anchor_parts = parts[:position]
            anchor = Path(*anchor_parts) if anchor_parts else Path(os.curdir)
            return anchor, parts[position:]
    if not path.name:
        return path, ()
    return path.parent, (path.name,)


def _open_anchor(anchor: Path) -> int:
    """Open the trusted anchor directory and return its descriptor.

    Symlinks *are* followed here, by design: the anchor is the layout the
    operator gave the checkout, not part of the artifact tree.

    Args:
        anchor: Directory returned by :func:`_trusted_anchor`.

    Returns:
        A read-only directory descriptor the caller must close.

    Raises:
        OSError: If the anchor cannot be opened - it does not exist, it is not
            a directory, or traversal is denied.
    """
    return os.open(anchor, os.O_RDONLY | _O_DIRECTORY)


def _reject_unverifiable(name: str) -> None:
    """Refuse ``.`` and ``..`` as a component of a verified artifact path.

    Args:
        name: One path component.

    Raises:
        ArtifactPathError: If the component is ``.`` or ``..``.
    """
    if name in _UNVERIFIABLE_COMPONENTS:
        raise ArtifactPathError(
            f"the artifact path component {name!r} cannot be verified no-follow"
        )


def _descend(parent_fd: int, name: str, *, create: bool) -> int:
    """Open one directory component under ``parent_fd`` without following links.

    Args:
        parent_fd: Descriptor of the already verified parent directory.
        name: The single component to enter.
        create: Whether to create the component when it is absent.  ``True``
            for the write-side helpers, ``False`` for the read-side ones, which
            must never bring an artifact directory into being.

    Returns:
        A read-only directory descriptor for the component, which the caller
        must close.

    Raises:
        ArtifactPathError: If the component is a symbolic link, is not a
            directory, or is ``.`` or ``..``.
        OSError: Anything else the platform reports - a denied traversal, a
            read-only filesystem, a name too long - unchanged, including the
            :exc:`FileExistsError` that :meth:`~pathlib.Path.mkdir` with
            ``exist_ok=True`` raises when a non-directory already occupies the
            path, so that failure keeps the exact type it had before
            verification was added.
    """
    _reject_unverifiable(name)
    if create:
        try:
            os.mkdir(name, dir_fd=parent_fd)
        except FileExistsError:
            # Something is already there.  lstat, not stat, so a symlink is
            # reported as itself rather than as whatever it points at.
            existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISLNK(existing.st_mode):
                raise ArtifactPathError(
                    f"the artifact path component {name!r} is a symbolic link; "
                    "refusing to create or write through it"
                ) from None
            if not stat.S_ISDIR(existing.st_mode):
                raise
    try:
        return os.open(
            name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=parent_fd
        )
    except NotADirectoryError:
        raise ArtifactPathError(
            f"the artifact path component {name!r} is not a directory"
        ) from None
    except OSError as error:
        if error.errno == errno.ELOOP:
            # O_NOFOLLOW reports a symlinked final component as ELOOP.  This is
            # the branch a link planted between the mkdir above and this open
            # lands in, which is the whole point of verifying the object the
            # descriptor holds rather than the name.
            raise ArtifactPathError(
                f"the artifact path component {name!r} is a symbolic link; "
                "refusing to traverse it"
            ) from None
        raise


def _descend_chain(anchor: Path, components: tuple[str, ...], *, create: bool) -> int:
    """Walk ``components`` beneath ``anchor``, verifying each one no-follow.

    Args:
        anchor: Trusted anchor directory, opened following symlinks.
        components: Directory components to enter, in order.
        create: Passed through to :func:`_descend`.

    Returns:
        A descriptor for the last directory reached, which the caller must
        close.  With no components that is the anchor's own descriptor.

    Raises:
        ArtifactPathError: From :func:`_descend`, for a refused component.
        OSError: From :func:`_open_anchor` or :func:`_descend`.  No descriptor
            is leaked on any path out of this function.
    """
    fd = _open_anchor(anchor)
    try:
        for name in components:
            next_fd = _descend(fd, name, create=create)
            try:
                os.close(fd)
            finally:
                # Ownership moves to the new descriptor whether or not closing
                # the old one succeeded, so the handler below closes exactly
                # one live descriptor.
                fd = next_fd
    except BaseException:
        os.close(fd)
        raise
    return fd


def _verified_parent_fd(path: Path, *, create: bool) -> tuple[int, str]:
    """Open the parent of ``path`` with every owned component verified.

    Args:
        path: The artifact file or directory whose parent is needed.
        create: Whether missing directories are created on the way down.

    Returns:
        The parent's descriptor - which the caller must close - and the final
        component of ``path`` as a name to be resolved against it.

    Raises:
        ArtifactPathError: For a refused component, or for a path that names no
            entry at all and therefore has no final component to open.
        OSError: As :func:`_descend_chain`.
    """
    anchor, components = _trusted_anchor(path)
    if not components:
        raise ArtifactPathError(
            "an artifact path must name an entry beneath a directory"
        )
    if create:
        # The anchor is trusted: create it the ordinary way, symlinks followed,
        # exactly as before verification was added.
        anchor.mkdir(parents=True, exist_ok=True)
    fd = _descend_chain(anchor, components[:-1], create=create)
    return fd, components[-1]


def _refuse_unsafe_entry(parent_fd: int, name: str) -> None:
    """Refuse a final component that is a link to somewhere else.

    This is what protects a writer that opens its own destination itself: an
    entry that is absent is fine - it is about to be created - and a plain
    regular file with one link is fine, because writing it in place overwrites
    the file the previous run left.  The two refusals are the two ways an entry
    can be somewhere else as well:

    * a **symbolic** link, because following it would write outside the
      artifact root; and
    * a **hard** link - a regular file whose link count exceeds one - because
      the entry *is* that other file, so writing the artifact would overwrite
      it and reading the artifact would disclose it, with no symlink anywhere
      in the path for a symlink check to find.  A run that empties the build
      output first never meets this case, since the file it then creates has
      one link; a ``--no-clean`` run over a prepared tree does.

    The test is made on the name, with :func:`os.stat` at
    ``follow_symlinks=False`` relative to the verified parent, so it settles
    what is there *now*.  The helpers that go on to open the entry re-establish
    both properties on the descriptor they obtained
    (:func:`_verify_open_artifact`); this check is what a caller holding only
    the returned path gets.

    Args:
        parent_fd: Descriptor of the verified parent directory.
        name: Final component to test.

    Raises:
        ArtifactPathError: If the entry exists and is a symbolic link or has
            more than one hard link, or if the component is ``.`` or ``..``.
        OSError: If the entry cannot be examined for any other reason.
    """
    _reject_unverifiable(name)
    try:
        existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(existing.st_mode):
        raise ArtifactPathError(
            f"the artifact entry {name!r} is a symbolic link; "
            "refusing to write or read through it"
        )
    if stat.S_ISREG(existing.st_mode) and existing.st_nlink > 1:
        raise ArtifactPathError(
            f"the artifact entry {name!r} has more than one hard link, so it "
            "is also a file elsewhere; refusing to write or read through it"
        )


def _verify_components_lexically(anchor: Path, components: tuple[str, ...]) -> None:
    """Portable stand-in for no-follow verification, used where it is absent.

    Walks the owned components and refuses any that is already a symbolic link.
    On its own this would be a check-then-open race, because the platform
    offers no way to tie the walk and the open into one operation; it is not
    left on its own.  The helpers that use it open the path and then call
    :func:`_verify_opened_by_name`, which refuses a descriptor that does not
    hold the object the name refers to - so a link planted between this walk
    and that open is caught, before a write truncates anything.  See the module
    docstring.

    Args:
        anchor: Trusted anchor directory.
        components: Owned components to check, in order.

    Raises:
        ArtifactPathError: For a component that is a symbolic link, or is ``.``
            or ``..``.
    """
    current = anchor
    for name in components:
        _reject_unverifiable(name)
        current = current / name
        if current.is_symlink():
            raise ArtifactPathError(
                f"the artifact path component {name!r} is a symbolic link; "
                "refusing to create, write or read through it"
            )


def _verify_open_artifact(fd: int, *, action: str) -> os.stat_result:
    """Interrogate an open descriptor and refuse anything but a lone regular file.

    The check both the read side and the write side make on the object they
    have actually opened, rather than on the name they opened it by - which is
    what makes the answer immune to any later change to the pathname.  Two
    conditions, and the second is not redundant with ``O_NOFOLLOW``:

    * It must be a **regular file**.  A directory, a FIFO or a device is
      refused: a FIFO in particular would block a writer for ever and hand a
      reader bytes no run ever produced.
    * It must have **exactly one link**.  ``O_NOFOLLOW`` refuses a *symbolic*
      link, and nothing about it refuses a **hard** link: an entry hard-linked
      to a file outside the artifact root is that file, so writing the
      artifact would overwrite it and reading the artifact would disclose it,
      with no symlink anywhere in the path.  A writer creates its own output,
      so one link is what a genuine artifact has; a second one means the entry
      was prepared by someone else.  A platform that does not report a link
      count - it comes back as ``0`` - is not second-guessed.

    Args:
        fd: An open descriptor.  Ownership stays with the caller, which closes
            it on the error paths below.
        action: ``"read"`` or ``"write"``, for the message only.

    Returns:
        The :func:`os.fstat` result, so a caller that needs the identity of the
        opened object does not have to stat it twice.

    Raises:
        ArtifactPathError: If the descriptor holds anything but a regular file
            with a single link.
        OSError: If the descriptor cannot be stat'ed.
    """
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise ArtifactPathError(
            f"the artifact entry is not a regular file; refusing to {action} it"
        )
    if info.st_nlink > 1:
        raise ArtifactPathError(
            "the artifact entry has more than one hard link, so it is also a "
            f"file elsewhere; refusing to {action} it"
        )
    return info


def _verify_opened_by_name(path: Path, fd: int, info: os.stat_result) -> None:
    """Confirm the descriptor holds the object ``path`` names right now.

    The fallback path's stand-in for ``O_NOFOLLOW``, and the reason that path
    is no longer a check-then-open race.  Where the platform cannot refuse a
    symlink *during* the open, the open is made anyway and the object it
    produced is then compared - by device and inode - with what a no-follow
    :func:`os.lstat` of the same name reports.  A link, or a different file,
    swapped in between the lexical check and the open therefore fails the
    comparison and is refused, rather than being read from or written to.

    The comparison is made **before** anything is written, which is why
    :func:`open_artifact_write` opens without ``O_TRUNC`` and truncates only
    after this returns: a mismatch detected after truncation would already have
    destroyed the wrong file.

    Args:
        path: The name the descriptor was opened by.
        fd: The open descriptor.  Ownership stays with the caller.
        info: The :func:`os.fstat` result for ``fd``.

    Raises:
        ArtifactPathError: If the name now refers to a symbolic link, or to an
            object other than the one held open, or names nothing at all.
        OSError: If the name cannot be examined.
    """
    try:
        named = os.lstat(path)
    except FileNotFoundError:
        raise ArtifactPathError(
            f"the artifact entry {path.name!r} disappeared while it was being "
            "opened; refusing to use it"
        ) from None
    if stat.S_ISLNK(named.st_mode) or (named.st_dev, named.st_ino) != (
        info.st_dev,
        info.st_ino,
    ):
        raise ArtifactPathError(
            f"the artifact entry {path.name!r} changed while it was being "
            "opened; refusing to use it"
        )


def _refuse_open_failure(error: OSError, name: str, *, action: str) -> None:
    """Translate the two open failures that mean "not an artifact" and re-raise.

    Args:
        error: The failure ``os.open`` reported.
        name: The entry it was opening, for the message.
        action: ``"read"`` or ``"write"``, for the message.

    Raises:
        ArtifactPathError: For ``ELOOP``, which is how ``O_NOFOLLOW`` reports a
            symbolic link at the final component, and for ``ENXIO``, which is
            how ``O_NONBLOCK`` reports a FIFO opened for writing with no reader
            - the case that would otherwise have blocked for ever.
        OSError: The original error, unchanged, for anything else.
    """
    if error.errno == errno.ELOOP:
        raise ArtifactPathError(
            f"the artifact entry {name!r} is a symbolic link; "
            f"refusing to {action} through it"
        ) from None
    if error.errno == errno.ENXIO:
        raise ArtifactPathError(
            f"the artifact entry {name!r} is not a regular file; "
            f"refusing to {action} it"
        ) from None
    raise error


def _truncated_write_stream(
    fd: int,
    destination: Path,
    *,
    binary: bool,
    encoding: str,
    newline: str,
    verify_by_name: bool,
) -> IO[Any]:
    """Verify a freshly opened write descriptor, empty it, and wrap it.

    The order is the whole point, and it is why :func:`open_artifact_write`
    opens without ``O_TRUNC``: verify what was opened, *then* discard its
    contents.  A caller of the plain builtin truncates first and learns what it
    truncated afterwards, which is too late to refuse anything.

    Args:
        fd: The descriptor just opened for writing.  This function takes
            ownership of it and closes it on every error path.
        destination: The name it was opened by, for the fallback comparison and
            for the messages.
        binary: Whether to hand back a binary stream.
        encoding: Text encoding, ignored when ``binary`` is set.
        newline: Newline translation, ignored when ``binary`` is set.
        verify_by_name: ``True`` on the fallback path, where the open could not
            refuse a symlink and the opened object is therefore compared with
            what the name reports now; ``False`` where ``O_NOFOLLOW`` under a
            held directory descriptor already established it.

    Returns:
        An open file object positioned at the start of an empty file.

    Raises:
        ArtifactPathError: If the descriptor does not hold a lone regular file,
            or - on the fallback path - no longer holds the object the name
            refers to.  Nothing has been written or truncated when this is
            raised.
        OSError: If the descriptor cannot be stat'ed, truncated or wrapped.
    """
    try:
        info = _verify_open_artifact(fd, action="write")
        if verify_by_name:
            _verify_opened_by_name(destination, fd, info)
        # Only now, with the object established, is the previous run's content
        # discarded - in place, because this module deletes nothing.
        os.ftruncate(fd, 0)
        if binary:
            return os.fdopen(fd, "wb")
        return os.fdopen(fd, "w", encoding=encoding, newline=newline)
    except BaseException:
        os.close(fd)
        raise


def _regular_file_stream(
    fd: int, source: Path | None = None, *, verify_by_name: bool = False
) -> BinaryIO:
    """Wrap an open descriptor as a binary stream, if it holds a regular file.

    The :func:`os.fstat` in :func:`_verify_open_artifact` is the load-bearing
    step of the read side: it interrogates the **object the descriptor already
    holds**, so its answer cannot be invalidated by anything that happens to
    the pathname afterwards.

    Args:
        fd: An open descriptor this function takes ownership of.
        source: The name the descriptor was opened by, required when
            ``verify_by_name`` is set.
        verify_by_name: ``True`` on the fallback path, where the open could not
            refuse a symlink, so the opened object is compared with what the
            name reports now - see :func:`_verify_opened_by_name`.

    Returns:
        A buffered binary stream over ``fd``.

    Raises:
        ArtifactPathError: If the descriptor holds anything but a regular file
            with a single link - a directory, a FIFO, a device, a hard-linked
            entry - or, on the fallback path, no longer holds the object the
            name refers to.  The descriptor is closed first.
        OSError: If the descriptor cannot be stat'ed or wrapped.  It is closed
            on every error path, so no descriptor is ever leaked.
    """
    try:
        info = _verify_open_artifact(fd, action="read")
        if verify_by_name and source is not None:
            _verify_opened_by_name(source, fd, info)
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def ensure_dir(path: Path | str) -> Path:
    """Create ``path`` as a directory, including missing parents, and return it.

    Idempotent: an existing directory is left untouched.  Used for ``target/``,
    ``target/.workers/`` and the PrettyReports output tree.

    Every component from the ``target`` component inward is created *and then
    opened* under the descriptor of its verified parent, with ``O_NOFOLLOW``,
    so a symlink standing in for ``target/`` or for any directory below it is
    refused instead of traversed - the case a ``--no-clean`` run, or a clean
    that failed part-way, leaves behind.  Components above the artifact root
    are the operator's own layout and are created the ordinary way; see the
    trusted-anchor rule in the module docstring.

    Args:
        path: Directory to create.

    Returns:
        The directory as a :class:`~pathlib.Path` - the same value as before
        verification was added, so the nine call sites that pass the result
        straight on are unaffected.

    Raises:
        ArtifactPathError: If a component from ``target`` inward is a symbolic
            link or is not a directory.  It is an :exc:`OSError`, so a caller
            that already handles one needs no change.
        OSError: If the directory cannot be created - a non-directory already
            occupies the path, the filesystem is read-only, traversal is
            denied.  The failure is deliberately not swallowed: a writer that
            cannot create its output directory must fail the run per the exit
            contract in AAP 0.4.1, not carry on silently.
    """
    directory = Path(path)
    anchor, components = _trusted_anchor(directory)
    if not components:
        # A bare filesystem root or ``.``: there is no component to verify, and
        # the ordinary create is both correct and a no-op.
        directory.mkdir(parents=True, exist_ok=True)
        return directory
    if not _NO_FOLLOW_SUPPORTED:
        _verify_components_lexically(anchor, components)
        directory.mkdir(parents=True, exist_ok=True)
        return directory
    anchor.mkdir(parents=True, exist_ok=True)
    fd = _descend_chain(anchor, components, create=True)
    os.close(fd)
    return directory


def ensure_parent(path: Path | str) -> Path:
    """Create the parent directory of ``path`` and return ``path`` itself.

    Called by each of the four artifact writers immediately before opening its
    output file, so that a writer never fails merely because ``target/`` was
    emptied by the ``--clean`` step.

    The parent is created and verified exactly as :func:`ensure_dir` does it,
    and the **final entry is checked too**: an entry that already exists as a
    symbolic link, or as a regular file with more than one hard link, is
    refused - both are ways for the destination to be a file somewhere else as
    well (:func:`_refuse_unsafe_entry`).  That check is what protects a writer
    which then opens the returned path itself, which is why it is not optional;
    a writer that wants the window closed completely takes the stream from
    :func:`open_artifact_write` instead and never names the path again.

    Args:
        path: File whose parent directory must exist.

    Returns:
        ``path`` as a :class:`~pathlib.Path`, unchanged, so the call can wrap an
        expression: ``ensure_parent(cucumber_json_path()).write_text(...)``.

    Raises:
        ArtifactPathError: If a component from ``target`` inward is a symbolic
            link or is not a directory, or if the final entry already exists as
            a symbolic link or as a hard-linked regular file.
        OSError: If the parent directory cannot be created; see
            :func:`ensure_dir` for why this is not swallowed.
    """
    destination = Path(path)
    if not _NO_FOLLOW_SUPPORTED:
        anchor, components = _trusted_anchor(destination)
        _verify_components_lexically(anchor, components)
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination
    fd, final = _verified_parent_fd(destination, create=True)
    try:
        _refuse_unsafe_entry(fd, final)
    finally:
        os.close(fd)
    return destination


def open_artifact_write(
    path: Path | str,
    *,
    binary: bool = False,
    encoding: str = "utf-8",
    newline: str = "\n",
) -> IO[Any]:
    """Open an artifact for writing, with no symlink anywhere in the path.

    A drop-in for the plain builtin the writers used to call::

        with open_artifact_write(destination) as stream:      # was:
            json.dump(document, stream)                       # open(destination, "w",
                                                              #      encoding="utf-8",
                                                              #      newline="\\n")

    The parent is created and verified exactly as :func:`ensure_parent` does it
    - through it, on the fallback path - and the final component is then opened
    ``O_WRONLY|O_CREAT|O_NOFOLLOW`` under the parent's descriptor, so the object
    written is the one that was verified, rather than a name that may have
    become a symlink in between (CWE-367).  The descriptor is then checked to
    hold a **regular file with a single link**, which is what refuses a
    destination hard-linked to a file outside the artifact root, and only then
    is the file truncated: ``O_TRUNC`` is deliberately not used, because a
    truncation performed by the open would destroy the wrong file before either
    check could refuse it.

    Truncating in place, rather than writing a temporary file and replacing the
    destination, is also deliberate: this module never deletes anything (see the
    module docstring and AAP 0.4.1, which give emptying the build output to
    ``app/cli.py``), so there is no temporary file to replace and no ``unlink``
    to perform - and with the destination established as a lone regular file
    inside a verified directory, replacement would add atomicity rather than
    safety.

    Args:
        path: Artifact file to write.
        binary: ``True`` for a binary stream, which ignores ``encoding`` and
            ``newline`` as the builtin does.
        encoding: Text encoding; ignored when ``binary`` is set.
        newline: Newline translation; ignored when ``binary`` is set.  The
            default writes ``\\n`` unchanged on every platform, which the
            artifacts require.

    Returns:
        An open file object, owned by the caller, who closes it - normally by
        using it as a context manager.

    Raises:
        ArtifactPathError: If a component from ``target`` inward, or the
            destination itself, is a symbolic link; if the destination is not a
            regular file, or has more than one hard link; or, on the fallback
            path, if the destination changed while it was being opened.  In
            every case nothing has been written or truncated.
        OSError: If the parent cannot be created or the file cannot be opened.
            No descriptor is leaked on any error path.
    """
    destination = Path(path)
    # O_TRUNC is deliberately absent from both branches: the file is opened,
    # the object that opened is verified, and only then is it truncated.  A
    # truncation performed by the open itself would have destroyed the wrong
    # file before any check could refuse it.
    flags = os.O_WRONLY | os.O_CREAT | _O_NOFOLLOW | _O_NONBLOCK

    if not _NO_FOLLOW_SUPPORTED:
        ensure_parent(destination)
        try:
            handle_fd = os.open(destination, flags, 0o666)
        except OSError as error:
            _refuse_open_failure(error, destination.name, action="write")
            raise
        handle = _truncated_write_stream(
            handle_fd,
            destination,
            binary=binary,
            encoding=encoding,
            newline=newline,
            verify_by_name=True,
        )
        return handle

    fd, final = _verified_parent_fd(destination, create=True)
    try:
        _refuse_unsafe_entry(fd, final)
        try:
            handle_fd = os.open(final, flags, 0o666, dir_fd=fd)
        except OSError as error:
            _refuse_open_failure(error, final, action="write")
            raise
    finally:
        os.close(fd)

    return _truncated_write_stream(
        handle_fd,
        destination,
        binary=binary,
        encoding=encoding,
        newline=newline,
        verify_by_name=False,
    )


def open_artifact_read(path: Path | str) -> BinaryIO:
    """Open an existing artifact file for reading, with no symlink in the path.

    The binary counterpart of :func:`open_artifact_write`, and the only way
    anything in this port should turn an artifact path into a read: every
    component from ``target`` inward is opened ``O_NOFOLLOW`` under its
    verified parent, and the descriptor that comes back is
    :func:`os.fstat`-checked to hold a regular file with a single link -
    refusing a directory, a FIFO, a device, and an entry hard-linked to a file
    outside the artifact root, which no symlink check can see.  Checking the
    open descriptor rather than the name is the point: it establishes what was
    actually opened, which no amount of re-stat'ing a pathname can.

    Nothing is created: a missing directory or a missing file is reported, not
    filled in.

    Args:
        path: Artifact file to read.

    Returns:
        An open buffered binary stream, owned by the caller.  Decode it, or use
        :func:`read_artifact_text`, which does that in one step.

    Raises:
        ArtifactPathError: If a component from ``target`` inward, or the entry
            itself, is a symbolic link; if the entry is not a regular file - a
            directory included - or has more than one hard link; or, on the
            fallback path, if the entry changed while it was being opened.
        FileNotFoundError: If the entry or one of its directories is absent.
        PermissionError: If the entry or one of its directories cannot be
            opened.  Both propagate as themselves, so a caller can tell an
            absent artifact from a refused one.
        OSError: Any other platform failure.
    """
    source = Path(path)
    if not _NO_FOLLOW_SUPPORTED:
        anchor, components = _trusted_anchor(source)
        _verify_components_lexically(anchor, components)
        try:
            handle_fd = os.open(source, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK)
        except OSError as error:
            _refuse_open_failure(error, source.name, action="read")
            raise
        return _regular_file_stream(handle_fd, source, verify_by_name=True)

    fd, final = _verified_parent_fd(source, create=False)
    try:
        _reject_unverifiable(final)
        try:
            handle_fd = os.open(
                final, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK, dir_fd=fd
            )
        except OSError as error:
            _refuse_open_failure(error, final, action="read")
            raise
    finally:
        os.close(fd)
    return _regular_file_stream(handle_fd)


def read_artifact_text(path: Path | str, *, encoding: str = "utf-8") -> str:
    """Read a verified artifact file as text.

    The replacement for :meth:`pathlib.Path.read_text` on an artifact path, and
    equivalent to it in what it returns: the bytes are decoded through a text
    wrapper with universal newline translation, so a file written with CRLF
    reads back with ``\\n`` exactly as ``read_text`` gives it.  What differs is
    how the file is opened - see :func:`open_artifact_read`.

    Args:
        path: Artifact file to read.
        encoding: Text encoding; the artifacts are UTF-8.

    Returns:
        The file's decoded contents.

    Raises:
        ArtifactPathError: As :func:`open_artifact_read`.
        UnicodeDecodeError: If the contents are not valid in ``encoding``,
            which is what ``read_text`` raises too.
        OSError: As :func:`open_artifact_read`.
    """
    with (
        open_artifact_read(path) as handle,
        io.TextIOWrapper(handle, encoding=encoding) as text,
    ):
        return text.read()


# --------------------------------------------------------------------------- #
# Untrusted-input resolution for the artifact route
# --------------------------------------------------------------------------- #


def _validated_artifact_components(name: str) -> tuple[str, ...] | None:
    """Validate an untrusted artifact name and return its path components.

    The whole of the string-level allowlist for ``GET /artifacts/<path:name>``,
    shared by :func:`resolve_artifact` and :func:`open_resolved_artifact` so
    that the validator and the opener can never disagree about what is
    allowed.  No filesystem access: this decides what the name is permitted to
    address, and the two callers decide, identically, whether the addressed
    object exists and is acceptable.

    Args:
        name: The raw request path segment.  Backslashes are normalised to
            forward slashes first, so a Windows-style separator cannot smuggle
            a component past validation.

    Returns:
        The components to join beneath the artifact root, with the authorized
        directory key already rewritten to the PrettyReports overview page, or
        ``None`` for every rejection.  Nothing is raised, and the attempted
        name is not echoed anywhere.
    """
    if not name:
        return None

    normalized = name.replace("\\", "/")

    # Absolute POSIX paths, UNC paths ("//host/share") and Windows drive
    # letters ("c:/...") are never served: the route addresses artifacts
    # relative to target/ only.
    if normalized.startswith("/"):
        return None
    if len(normalized) >= 2 and normalized[1] == ":":
        return None

    components = normalized.split("/")
    # Exactly one trailing slash denotes a directory, and the PrettyReports
    # directory is the only directory the route serves anything for, so that
    # one slash is dropped here and the name is required to be that directory
    # below.  A second trailing slash leaves an empty component behind, which
    # the loop rejects - "cucumber//" is not an authorized spelling.
    trailing_slash = components[-1] == ""
    if trailing_slash:
        components = components[:-1]
    for component in components:
        # An empty component means a doubled separator.  A leading dot covers
        # "." and ".." traversal and every dot-directory, ".workers" foremost.
        if not component or component.startswith("."):
            return None

    relative = "/".join(components)
    if relative == PRETTY_REPORTS_DIR_NAME:
        # A request for the PrettyReports directory is served its overview
        # page.  This is the only directory rewrite there is, and it is made
        # here, so no caller needs - or is permitted - a directory fallback of
        # its own.
        components = [
            PRETTY_REPORTS_DIR_NAME,
            PRETTY_HTML_SUBDIR,
            PRETTY_OVERVIEW_INDEX,
        ]
    elif trailing_slash:
        # Any other directory-shaped request is a 404, including one that would
        # otherwise name a file.
        return None
    elif relative not in _ALLOWED_FILE_KEYS and not relative.startswith(
        f"{PRETTY_REPORTS_DIR_NAME}/"
    ):
        return None
    return tuple(components)


def _verify_artifact_no_follow(path: Path) -> None:
    """Refuse a resolved artifact path with a symlink from ``target`` inward.

    The validation-only counterpart of :func:`open_artifact_read`, for
    :func:`resolve_artifact`, which returns a path rather than a handle.  It
    applies the same trusted-anchor rule as the write-side helpers, so the
    validator and :func:`open_resolved_artifact` cannot reach opposite
    conclusions about the same request.

    Args:
        path: The candidate artifact path, beneath the artifact root.

    Raises:
        ArtifactPathError: If a component from ``target`` inward, or the entry
            itself, is a symbolic link.
        OSError: If a component cannot be examined.  Nothing is created.
    """
    if not _NO_FOLLOW_SUPPORTED:
        anchor, components = _trusted_anchor(path)
        _verify_components_lexically(anchor, components)
        return
    fd, final = _verified_parent_fd(path, create=False)
    try:
        _refuse_unsafe_entry(fd, final)
    finally:
        os.close(fd)


def resolve_artifact(name: str, base: Path | str | None = None) -> Path | None:
    """Resolve an allowlisted artifact name from an HTTP request, or reject it.

    Backs ``GET /artifacts/<path:name>`` (AAP 0.3.1), which serves *"an
    allowlisted artifact only: ``cucumber-reports.html``, ``cucumber.json``,
    ``rerun.txt``, or any path under ``cucumber/``.  A request naming the
    ``cucumber/`` directory serves ``cucumber-html-reports/overview-features.html``.
    Everything else is 404 - any other directory, any absent file, any path
    resolving outside the artifact root, and ``.workers/`` in particular, which
    must never be reachable."*

    Every rejection returns ``None`` and no rejection raises, which is what lets
    ``app/errors.py`` turn all of them into one plain 404 that leaks no
    filesystem path and is never a 403.  The attempted path is deliberately not
    echoed anywhere.

    Rejected, in this order: an empty name; an absolute path or a UNC prefix; a
    Windows drive letter; an empty path component; any component beginning with
    a dot, which covers ``.`` and ``..`` traversal and ``.workers`` alike; a
    directory-shaped name ending in a slash, unless it is the ``cucumber``
    directory; a name that is not on the allowlist; a path whose resolved
    location escapes ``target/``, which catches symlink escapes because the
    check is made after :meth:`~pathlib.Path.resolve` has followed links; a
    path that cannot be resolved at all, such as one carrying an embedded null
    byte; a path that does not exist; a path that exists but is a directory -
    except for the ``cucumber`` directory itself, which is rewritten to its
    overview page; and a path with a symbolic link at any component from
    ``target`` inward, or a hard-linked entry at the end of it, refused by the
    same trusted-anchor rule the write-side helpers apply, so that this
    validator and :func:`open_resolved_artifact` can never disagree about one
    request.

    **The caller contract, which is the whole of it.**  A caller passes the raw
    request segment through unchanged, and does nothing to it before or after:

    * The only authorized directory spellings are the bare ``cucumber`` key and
      that key with **exactly one** trailing slash.  Both are rewritten here to
      the PrettyReports overview page.  Every other trailing-separator spelling
      - ``cucumber//``, ``cucumber///``, any number of them, and any other
      directory-shaped name - is rejected here, because the second slash leaves
      an empty component behind and an empty component is never allowed.
    * So a caller **must not** strip, collapse or otherwise normalise trailing
      separators.  ``rstrip("/")`` in a caller accepts every one of those
      spellings and hands this function a name it would have refused, which
      defeats the empty-component rule rather than implementing it.  AAP 0.3.1
      authorizes exactly two spellings; everything else is a 404.
    * And a caller **must not** add a directory fallback after resolution - no
      "if this turned out to be a directory, serve the overview page".  The one
      authorized directory key is already mapped here, so a generic fallback
      can only map something else: a request for ``cucumber.json`` that becomes
      a directory would be answered with the overview *HTML*, which is neither
      the JSON asked for nor the 404 the contract requires.
    * A caller that is about to *open* what it resolved should call
      :func:`open_resolved_artifact` instead, which validates and opens in one
      operation.  This function returns a path, and a path is only ever a
      statement about the past.

    Args:
        name: The raw request path segment.  Backslashes are normalised to
            forward slashes first, so a Windows-style separator cannot smuggle
            a component past validation.
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The **resolved** path of the file to serve - the checked object itself,
        not the lexical path it was addressed by, so a caller cannot re-resolve
        a different object than the one that was validated.  It compares equal
        to the resolved form of the matching accessor - so, for the JSON key,
        to ``cucumber_json_path(base).resolve()``, and for the directory key to
        ``pretty_reports_index_path(base).resolve()``.  Those differ from the
        accessor's own value only by the symlinks resolved *above* the artifact
        root, which are the operator's own layout.  ``None`` if the request is
        rejected for any reason.  Nothing is created and nothing is written.
    """
    components = _validated_artifact_components(name)
    if components is None:
        return None

    root = target_root(base)
    candidate = root.joinpath(*components)
    try:
        # resolve() collapses any remaining relative segments and follows
        # symlinks, so a single containment check defeats both traversal and
        # symlink escape - including a Windows component bearing a drive letter,
        # which joinpath would otherwise treat as a fresh anchor.  It touches
        # the filesystem, hence the guard: OSError for an unreadable or too-long
        # path, ValueError for a name carrying an embedded null byte, which the
        # platform's stat call rejects outright.
        resolved_root = root.resolve()
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return None
    if not resolved.is_relative_to(resolved_root):
        return None
    if not resolved.is_file():
        # Covers both an absent path and an existing directory.
        return None
    try:
        # A link anywhere from target/ inward is refused outright, not merely
        # required to land inside the root: the route must serve the artifacts
        # the writers wrote, and a link in the middle of the tree is not one of
        # them.  Any failure here is a rejection, never an exception.
        _verify_artifact_no_follow(candidate)
    except (OSError, ValueError):
        return None
    return resolved


def open_resolved_artifact(
    name: str, base: Path | str | None = None
) -> tuple[Path, BinaryIO] | None:
    """Validate an untrusted artifact name and open the file, in one operation.

    The entry point for ``GET /artifacts/<path:name>`` (AAP 0.3.1), and the
    reason the route needs nothing else: it applies exactly the allowlist of
    :func:`resolve_artifact` - the same code, so the two cannot drift - and
    then opens the file it validated, under a held directory descriptor and
    with ``O_NOFOLLOW``, before returning.  There is no window between the
    check and the open for a symlink to be swapped into (CWE-367), and the
    caller never has to name the path again: it serves the handle.

    A caller passes the raw request segment through unchanged; the contract in
    :func:`resolve_artifact` applies here in full, including the two authorized
    directory spellings and the prohibition on a directory fallback.

    Args:
        name: The raw request path segment, untrusted.
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The resolved path and an open binary stream over it - the caller owns
        the stream and closes it, normally by handing it to the response - or
        ``None`` for every rejection, including an absent file, a directory, a
        symbolic link, a path escaping the artifact root, and a name off the
        allowlist.

    Raises:
        Nothing.  Ever.  :exc:`ArtifactPathError`, every other
        :exc:`OSError` and :exc:`ValueError` are absorbed into ``None``, and
        neither the attempted name nor any filesystem path appears in a message
        or a log record here, so ``app/errors.py`` answers one plain 404 that
        tells a prober nothing about the layout.
    """
    components = _validated_artifact_components(name)
    if components is None:
        return None

    root = target_root(base)
    candidate = root.joinpath(*components)
    try:
        resolved_root = root.resolve()
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return None
    if not resolved.is_relative_to(resolved_root):
        # Made before anything is opened, so a lexically escaping name never
        # reaches an open call on a platform without O_NOFOLLOW.
        return None

    try:
        handle = open_artifact_read(candidate)
    except (OSError, ValueError):
        # ArtifactPathError for a symlink or a non-regular file,
        # FileNotFoundError for an absent one, PermissionError for an
        # unreadable one: all one 404.
        return None
    # The open is the decisive step.  Every component from target/ inward was
    # verified not to be a symlink in the operation that produced this handle,
    # and none of the validated components is empty, dot-prefixed or a
    # separator, so the object now held open is the one at ``resolved`` - and no
    # later change to the pathname can alter what the caller serves.
    return resolved, handle


# --------------------------------------------------------------------------- #
# Feature URI normalisation
# --------------------------------------------------------------------------- #


def normalize_feature_uri(value: str) -> str:
    """Rewrite a legacy feature-directory prefix to the one this port emits.

    AAP deviation 1 moves the features from ``src/main/resources/features/`` to
    ``features/`` while preserving their filenames.  The golden fixtures under
    ``tests/fixtures/`` are stored verbatim, with the legacy prefix, so the
    writer tests compare against them through this single helper and no test
    hard-codes either prefix (AAP 0.4.1).

    The rewrite is a prefix substitution on the string, so anything following
    the path - notably the ``:9:24`` line-number suffixes of the rerun manifest
    (AAP 0.6) - is preserved by construction, and a leading ``file:`` scheme is
    carried through untouched.

    Args:
        value: A feature URI or rerun-manifest entry, with or without the
            ``file:`` scheme and with or without trailing line numbers.

    Returns:
        The value with a leading legacy feature prefix replaced by
        ``features/``.  Anything else is returned unchanged, which makes the
        function idempotent: an already-normalised value comes back as it went
        in.  A path that merely contains the legacy segment somewhere other than
        at the start of its path portion is left alone.

    Examples:
        >>> normalize_feature_uri("file:src/main/resources/features/Crm.feature")
        'file:features/Crm.feature'
        >>> normalize_feature_uri("file:src/main/resources/features/Crm.feature:9:24")
        'file:features/Crm.feature:9:24'
        >>> normalize_feature_uri("features/Crm.feature")
        'features/Crm.feature'
    """
    if value.startswith(FILE_URI_SCHEME):
        scheme = FILE_URI_SCHEME
        remainder = value[len(FILE_URI_SCHEME) :]
    else:
        scheme = ""
        remainder = value

    if not remainder.startswith(LEGACY_FEATURES_PREFIX):
        return value

    tail = remainder[len(LEGACY_FEATURES_PREFIX) :]
    return f"{scheme}{NORMALIZED_FEATURES_PREFIX}{tail}"


# --------------------------------------------------------------------------- #
# Package-relative locators.
#
# These four take no ``base``: they are package-relative, not working-directory-
# relative, which is the distinction most likely to be misused.  They are
# derived from ``__file__`` so they stay correct in a worker process running
# from any directory, and they are needed outside any Flask application
# context - ``app/reporting/pretty_reports.py`` copies the vendored assets into
# the emitted tree and ``app/reporting/html_report.py`` inlines the stylesheet
# and script at write time, neither with Flask's ``static_folder`` available.
# --------------------------------------------------------------------------- #


def package_root() -> Path:
    """Return the ``app/`` package directory.

    Note this is the installed package directory, **not** the repository root
    and not the working directory: it is derived from this module's
    ``__file__`` (``app/utils/paths.py`` -> ``app/``), so it remains correct
    whatever directory the process runs in and whether the package is used from
    the source tree or from an installed wheel.

    Returns:
        The absolute path of the ``app`` package directory.
    """
    return Path(__file__).resolve().parent.parent


def templates_dir() -> Path:
    """Return the ``app/templates`` directory.

    Package-relative, not working-directory-relative.  Holds the artifact
    template sets, the HTTP view templates, the shared partials and the error
    pages (AAP 0.3.1); declared as package data in ``pyproject.toml`` so it
    travels with an installed wheel.

    Returns:
        The absolute path of the template root.
    """
    return package_root() / "templates"


def static_dir() -> Path:
    """Return the ``app/static`` directory.

    Package-relative, not working-directory-relative.  Holds ``css/main.css``
    and ``js/report.js``, which ``app/reporting/html_report.py`` inlines into
    the single-page artifact, plus the vendored asset set.

    Returns:
        The absolute path of the static root.
    """
    return package_root() / "static"


def vendor_dir() -> Path:
    """Return the ``app/static/vendor`` directory.

    Package-relative, not working-directory-relative.  Holds the Bootstrap,
    jQuery, Chart.js, tablesorter, Moment, icon-font and favicon files that
    ``app/reporting/pretty_reports.py`` copies into the emitted
    ``target/cucumber/cucumber-html-reports/`` tree, so those pages render
    offline from a CI workspace (AAP 0.3.4).

    Returns:
        The absolute path of the vendored asset directory.
    """
    return static_dir() / "vendor"
