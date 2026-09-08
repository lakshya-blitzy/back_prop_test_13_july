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
* **This module creates directories but never deletes anything.**  There is no
  ``rmtree``, ``unlink``, ``rmdir`` or "clean" helper here, by design: AAP 0.4.1
  assigns emptying ``target/`` (the ``--clean`` step) and tearing down
  ``target/.workers/`` to ``app/cli.py``.  This module's job is to hand those
  callers the path.  Do not add a delete helper here.
"""

import os
from pathlib import Path
from typing import Final, NamedTuple

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
    "ArtifactSpec",
    "artifact_path",
    "cucumber_json_path",
    "cucumber_reports_html_path",
    "ensure_dir",
    "ensure_parent",
    "features_dir",
    "iter_worker_result_paths",
    "normalize_feature_uri",
    "package_root",
    "pretty_reports_dir",
    "pretty_reports_html_dir",
    "pretty_reports_index_path",
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

    Args:
        key: An :class:`ArtifactSpec`, or one of its ``key`` values -
            ``"cucumber-reports.html"``, ``"cucumber.json"``, ``"rerun.txt"`` or
            ``"cucumber"``.
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The artifact path.  For the ``"cucumber"`` key this is the
        PrettyReports *directory*, matching :attr:`ArtifactSpec.is_dir`.

    Raises:
        KeyError: If ``key`` is not one of the four artifact keys.  Unlike
            :func:`resolve_artifact`, which validates untrusted request input
            and returns ``None``, this function is called with a key chosen in
            code, so an unknown key is a programming error and is raised
            loudly.
    """
    if isinstance(key, ArtifactSpec):
        spec = key
    else:
        try:
            spec = _SPECS_BY_KEY[key]
        except KeyError:
            raise KeyError(
                f"unknown artifact key {key!r}; expected one of {tuple(_SPECS_BY_KEY)}"
            ) from None
    return _base(base).joinpath(*spec.relpath.split("/"))


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

    Args:
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        A tuple of the worker result files, sorted by filename - which, thanks
        to the zero-padded shard index in :func:`worker_result_path`, is also
        numeric order within a process id.  An empty tuple is returned when the
        directory is absent, is not a directory, or cannot be listed; a missing
        directory simply means no worker has written yet and is never an error.
    """
    directory = workers_dir(base)
    try:
        entries = [
            entry
            for entry in directory.iterdir()
            if entry.name.startswith(_WORKER_RESULT_PREFIX)
            and entry.name.endswith(_WORKER_RESULT_SUFFIX)
            and entry.is_file()
        ]
    except OSError:
        return ()
    return tuple(sorted(entries, key=lambda entry: entry.name))


# --------------------------------------------------------------------------- #
# Directory creation.  This module creates directories and never deletes
# anything - see the module docstring.
# --------------------------------------------------------------------------- #


def ensure_dir(path: Path | str) -> Path:
    """Create ``path`` as a directory, including missing parents, and return it.

    Idempotent: an existing directory is left untouched.  Used for
    ``target/``, ``target/.workers/`` and the PrettyReports output tree.

    Args:
        path: Directory to create.

    Returns:
        The directory as a :class:`~pathlib.Path`.

    Raises:
        OSError: If the directory cannot be created - for example because a
            non-directory already occupies the path, or the filesystem is
            read-only.  The failure is deliberately not swallowed: a writer that
            cannot create its output directory must fail the run per the exit
            contract in AAP 0.4.1, not carry on silently.
    """
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def ensure_parent(path: Path | str) -> Path:
    """Create the parent directory of ``path`` and return ``path`` itself.

    Called by each of the four artifact writers immediately before opening its
    output file, so that a writer never fails merely because ``target/`` was
    emptied by the ``--clean`` step.

    Args:
        path: File whose parent directory must exist.

    Returns:
        ``path`` as a :class:`~pathlib.Path`, unchanged, so the call can wrap an
        expression: ``ensure_parent(cucumber_json_path()).write_text(...)``.

    Raises:
        OSError: If the parent directory cannot be created; see
            :func:`ensure_dir` for why this is not swallowed.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


# --------------------------------------------------------------------------- #
# Untrusted-input resolution for the artifact route
# --------------------------------------------------------------------------- #


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
    byte; a path that does not exist; and a path that exists but is a
    directory - except for the ``cucumber`` directory itself, which is
    rewritten to its overview page.

    Args:
        name: The raw request path segment.  Backslashes are normalised to
            forward slashes first, so a Windows-style separator cannot smuggle
            a component past validation.
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The path of the file to serve - built from the artifact root exactly as
        the other accessors build theirs, so it compares equal to, say,
        :func:`cucumber_json_path` or :func:`pretty_reports_index_path` - or
        ``None`` if the request is rejected for any reason.  Nothing is created
        and nothing is written.
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
    # A trailing slash denotes a directory, and the PrettyReports directory is
    # the only directory the route serves anything for, so the slash is dropped
    # here and the name is required to be that directory below.
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
        # A request for the PrettyReports directory is served its overview page.
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

    root = target_root(base)
    candidate = root.joinpath(*components)
    try:
        # resolve() collapses any remaining relative segments and follows
        # symlinks, so a single containment check defeats both traversal and
        # symlink escape.  It touches the filesystem, hence the guard: OSError
        # for an unreadable or too-long path, ValueError for a name carrying an
        # embedded null byte, which the platform's stat call rejects outright.
        resolved_root = root.resolve()
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return None
    if not resolved.is_relative_to(resolved_root):
        return None
    if not resolved.is_file():
        # Covers both an absent path and an existing directory.
        return None
    return candidate


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
