"""Single source of truth for every filesystem path used by the Testinium-QA port.

Path drift is how this port breaks silently, so ownership is explicit (AAP
0.4.2): every writer, the artifact route, the clean step and the per-worker
invocation take their paths from here, and no other module has a path literal.

The four artifacts are the Java runner's Cucumber plugin targets
(``CukesRunner.java:9-14``, the source AAP 0.4.1 anchors this module on): the
self-contained HTML page, the JSON report, the rerun manifest and the
PrettyReports *directory*, whose pages sit one level deeper (AAP 0.3.4).

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
* **The only thing this module ever removes is scratch it created itself.**
  Publishing a *directory* artifact is a staged build and a rename, so the
  publication API below creates a dot-prefixed staging sibling and removes it
  again inside the same call, under the directory descriptor it verified
  (:class:`ArtifactDirectoryPublication`).  Nothing else is removable from
  here: no published artifact, no ``target/`` and no ``target/.workers/``.
  Emptying the build output (the ``--clean`` step) stays with ``app/cli.py``
  and the per-worker directory lifecycle stays with
  ``app/services/test_run_service.py``, exactly as AAP 0.4.1 assigns them.

Artifact I/O: the write authority, and the trusted anchor
---------------------------------------------------------
``target/`` is generated output that outlives a run - ``run-tests --no-clean``
keeps it, and a clean that failed part-way leaves some of it - so a symbolic
link can be sitting at ``target/``, at ``target/cucumber/`` or at
``target/cucumber.json`` by the time a writer opens its output, and
``GET /artifacts/<path:name>`` turns request input into a filesystem read.
Validating a *pathname* and then opening it leaves a window between the two in
which the name can come to mean a different object (CWE-367/CWE-59), so
**everything that creates, writes or publishes an artifact does it through one
of the three write entry points here, and none of them hands back a pathname
for the caller to re-open**:

* :func:`open_artifact_write` opens an artifact for writing in place and
  returns the stream.  ``app/reporting/cucumber_json.py``,
  ``app/reporting/rerun_report.py`` and ``app/reporting/events.py`` - the
  worker-intermediate writer and the collector's own output stream - write
  through it.
* :func:`publish_artifact_file` writes a *new* file into the destination's own
  verified directory and renames it onto the destination when the caller's
  block completes, so a reader sees the previous complete artifact or the new
  one and never a partial page.  ``app/reporting/html_report.py`` publishes
  ``target/cucumber-reports.html`` through it.
* :func:`begin_directory_publication` returns a
  :class:`ArtifactDirectoryPublication`: a staging directory whose files are
  created under a held descriptor, swapped into place by rename, and whose
  scratch is removed through the same descriptor.
  ``app/reporting/pretty_reports.py`` publishes the ``target/cucumber`` tree
  through it.
* The read side is :func:`open_artifact_read` and :func:`read_artifact_text`,
  which return an already-verified regular file to a caller that holds an
  artifact path, plus the pair that serves ``GET /artifacts/<path:name>``:
  :func:`resolve_artifact` validates an untrusted request name and returns the
  path it validated, and :func:`open_resolved_artifact` validates and opens in
  one operation.  The two are **not** interchangeable, and the rule is the same
  one as on the write side: a returned path is only a statement about the
  past, so a caller that is about to open what it resolved takes the
  one-operation opener, and a caller that genuinely needs the name - to list
  it, to report it - takes the validator.
* :func:`ensure_dir` and :func:`ensure_parent` create and verify directory
  components under a held directory descriptor for a caller that needs the
  directory itself rather than a stream, and :func:`ensure_parent` also
  refuses a final entry that already exists as a symbolic link or a hard link.
  Neither is a substitute for the three write entry points: a caller that goes
  on to name the path again in a plain ``open`` reopens it, which is the window
  the entry points close.
* :exc:`ArtifactPathError` is what a refused component raises.  It subclasses
  :exc:`OSError`, so a writer's existing ``except OSError`` maps it to the
  writer-failure exit class of AAP 0.4.1 with no change and no new exit class.

Permissions: what the generated artifacts are created as
--------------------------------------------------------
The artifacts carry the run's evidence - failure screenshots, step arguments
substituted from the Examples tables, the configured URLs - so they are not
world-readable build output (CWE-732/CWE-359).  Every directory this module
creates inside the artifact root is created :data:`ARTIFACT_DIR_MODE` and every
file :data:`ARTIFACT_FILE_MODE`, and because a mode argument only applies to an
object the call actually creates, each artifact opened for writing and each
owned directory descended on the way to one is also **tightened through its own
descriptor**: the group and other bits of :data:`ARTIFACT_MODE_MASK` are
cleared with :func:`os.fchmod`, so a file a previous run left at ``0644``, or a
``target/`` the operator created at ``0755``, does not keep those bits.  The
policy is applied per object rather than by mutating the process umask, which a
library has no business doing to its host: a run-wide ``umask(0o077)`` would
also silently change every unrelated file the interpreter goes on to create.
Where the platform has no :func:`os.fchmod` - Windows, whose permissions are
ACLs rather than POSIX modes - the mode policy is not expressible and is not
emulated; that is the one part of this section a Windows run does not get.

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

**Portability, and what the fallback binds instead of a descriptor.**
``O_NOFOLLOW`` and the ``dir_fd`` parameter are POSIX-only, while AAP 0.8
requires Windows, Linux and macOS, so availability is detected rather than
assumed.  Where the primitives are missing, the helpers cannot hold a parent
open - but they do not fall back to a check on a *name*, which a junction or a
swap would defeat.  They bind the objects instead
(:class:`_FallbackAnchor`), and there are three parts to it:

* **every reparse point is refused, not just a symbolic link.**  A Windows
  *junction* is a directory reparse point that :meth:`~pathlib.Path.is_symlink`
  answers ``False`` for while :func:`os.stat` follows it, so the test is on
  ``st_file_attributes`` and ``st_reparse_tag`` as well as on ``S_ISLNK``: an
  owned component that is any kind of reparse point is refused outright.
* **the parent chain is bound by object identity.**  Each owned directory
  component is :func:`os.lstat`-ed and its ``(st_dev, st_ino)`` recorded, and
  that snapshot is re-established at both ends of every operation that could
  cross the boundary: immediately before every destructive step - the
  truncation, the rename, the disposal - and again immediately **after every
  open**, before the stream is handed to the caller, so a read cannot disclose
  a file that a substituted chain supplied either.  The two request entry
  points bind the chain *before* they resolve an untrusted name and check
  containment, and re-establish it afterwards, so the window their own
  validation opens is covered by the same comparison.  A directory swapped out
  from under any of that fails the comparison and the operation is refused.
* **it fails closed where identity is unobtainable.**  A filesystem that
  reports ``st_ino == 0`` - some network shares, some FAT volumes - offers
  nothing to bind, so rather than proceeding on an unverifiable name the
  helpers raise :exc:`ArtifactPathError` and the run reports a writer failure.
* **no directory is descended by name.**  A recursive removal by pathname
  cannot be made safe on this branch at all: a directory substituted between
  the stat that approves it and the listing that walks it sends the removal
  into the substitute, and a deletion outside the artifact tree cannot be
  taken back once the comparison catches it.  So the fallback does not walk.
  Scratch is disposed of only where that needs no walk - an entry that is not
  a directory is unlinked, an empty directory is removed - and a directory
  that still has contents is **renamed aside** to a dot-prefixed name no
  request can reach, for the ``--clean`` step to remove on the next run
  (:func:`_detach_tree_by_name`).  Publication scratch can therefore outlive a
  call on this branch, which is the price of that guarantee.

An artifact opened on this branch is additionally compared with what its own
name reports *now*, by device and inode, before anything is written
(:func:`_verify_opened_by_name`), and the write path truncates last, so a
mismatch is caught while the wrong file is still intact.

**What this branch does and does not promise, exactly.**  It is *detection and
refusal*, not prevention, and the distinction is worth stating plainly because
the two are not interchangeable.  With the primitives present, ``O_NOFOLLOW``
under a held directory descriptor makes the redirected open impossible: the
name is never resolved a second time.  Without them the open still happens by
name, and what the checks establish is that it landed on the wrong object -
before a byte is read out of it or written into it, and before anything is
renamed or removed - so the outcome of a won race is a refused run, never a
file outside the artifact root that was read, written, or deleted.  One thing
neither branch can detect is a substitution that predates the run: a directory
already standing in for ``target/`` when the first component is bound is
indistinguishable from the genuine one, on POSIX exactly as on Windows, because
there is nothing earlier to compare it with.  That is a workspace that was
already compromised before the port was invoked, and it is outside what a path
helper can be asked to settle.
"""

import errno
import io
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, BinaryIO, Final, NamedTuple

__all__ = [
    "ARTIFACT_DIR_MODE",
    "ARTIFACT_FILE_MODE",
    "ARTIFACT_MODE_MASK",
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
    "PUBLICATION_STAGING_INFIX",
    "PUBLICATION_SUPERSEDED_INFIX",
    "RERUN_TXT_NAME",
    "RERUN_TXT_RELPATH",
    "TARGET_DIR_NAME",
    "WORKERS_DIR_NAME",
    "ArtifactDirectoryPublication",
    "ArtifactPathError",
    "ArtifactSpec",
    "PublicationScratch",
    "artifact_path",
    "begin_directory_publication",
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
    "publish_artifact_file",
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

#: Build output directory.  ``mvn clean test`` emptied it; ``run-tests
#: --clean`` empties it now (AAP 0.4.1).
TARGET_DIR_NAME: Final[str] = "target"

#: Gherkin feature directory at the repository root.  The Java runner declared
#: ``features = "src/main/resources/features"`` (``CukesRunner.java:15``); AAP
#: deviation 1 moves the features to ``features/`` while preserving filenames,
#: because the filenames appear in the JSON ``uri``, in the rerun manifest and
#: in user commands.
FEATURES_DIR_NAME: Final[str] = "features"

#: Per-worker intermediate results directory, beneath ``target/``.  Dot-
#: prefixed so it is trivially distinguishable from published artifacts, and
#: unreachable through the HTTP artifact route (AAP 0.3.1).
WORKERS_DIR_NAME: Final[str] = ".workers"

PRETTY_REPORTS_DIR_NAME: Final[str] = "cucumber"

#: Sub-directory the PrettyReports generator writes its pages and assets into
#: (AAP 0.3.4).
PRETTY_HTML_SUBDIR: Final[str] = "cucumber-html-reports"

PRETTY_OVERVIEW_INDEX: Final[str] = "overview-features.html"

CUCUMBER_REPORTS_HTML_NAME: Final[str] = "cucumber-reports.html"

#: File name of the Cucumber-JVM JSON report (``CukesRunner.java:11``).  The only
#: artifact with a machine consumer in CI - the Jenkins publisher reads it and
#: nothing else (``Jenkins:15``).
CUCUMBER_JSON_NAME: Final[str] = "cucumber.json"

#: File name of the rerun manifest (``CukesRunner.java:12``), read back by
#: ``run-tests --rerun``.
RERUN_TXT_NAME: Final[str] = "rerun.txt"

# The four relative paths are strings rather than Path objects on purpose: they
# are quoted in README.md, listed by the ``GET /`` route and must agree
# byte-for-byte with the Jenkins publisher's ``fileIncludePattern``.  They are
# joined with an explicit "/" so they render identically on POSIX and Windows -
# a backslash here would break both the README and the publisher glob.
CUCUMBER_REPORTS_HTML_RELPATH: Final[str] = (
    f"{TARGET_DIR_NAME}/{CUCUMBER_REPORTS_HTML_NAME}"
)

#: ``target/cucumber.json`` (``CukesRunner.java:11``).  Equal to the Jenkins
#: publisher's narrowed ``fileIncludePattern`` (``Jenkins:15``).
CUCUMBER_JSON_RELPATH: Final[str] = f"{TARGET_DIR_NAME}/{CUCUMBER_JSON_NAME}"

RERUN_TXT_RELPATH: Final[str] = f"{TARGET_DIR_NAME}/{RERUN_TXT_NAME}"

PRETTY_REPORTS_RELPATH: Final[str] = f"{TARGET_DIR_NAME}/{PRETTY_REPORTS_DIR_NAME}"

#: Scheme prefix every feature URI carries in the JSON report and in the rerun
#: manifest, e.g. ``"file:features/Crm.feature"`` (AAP 0.6).
FILE_URI_SCHEME: Final[str] = "file:"

#: Feature-directory prefix used by the Java implementation, kept verbatim in
#: the golden fixtures.  The trailing slash is deliberate, so removing it
#: yields a bare filename.
LEGACY_FEATURES_PREFIX: Final[str] = "src/main/resources/features/"

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


#: The four artifacts, in the plugin order of ``CukesRunner.java:9-14`` (html,
#: json, rerun, pretty).  The ``GET /`` route lists them in this order.
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
    """Return ``<base>/target/cucumber-reports.html`` (``CukesRunner.java:10``).

    The single self-contained HTML page written by
    ``app/reporting/html_report.py``.

    Args:
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The HTML report path.
    """
    return target_root(base) / CUCUMBER_REPORTS_HTML_NAME


def cucumber_json_path(base: Path | str | None = None) -> Path:
    """Return ``<base>/target/cucumber.json`` (``CukesRunner.java:11``).

    Written by ``app/reporting/cucumber_json.py`` and read by the Jenkins
    Cucumber publisher (``Jenkins:15``) and by ``app/web/routes.py``.

    Args:
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The JSON report path.
    """
    return target_root(base) / CUCUMBER_JSON_NAME


def rerun_txt_path(base: Path | str | None = None) -> Path:
    """Return ``<base>/target/rerun.txt`` (``CukesRunner.java:12``).

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
    """Return ``<base>/target/cucumber`` (``CukesRunner.java:13``).

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

    A supplied :class:`ArtifactSpec` is **not** trusted to describe its own
    location: it is a plain :class:`~typing.NamedTuple`, so a hand-built
    ``ArtifactSpec("x", "../../secret", False)`` would otherwise hand back a
    path outside ``base``.  A spec is looked up by its own ``key`` and required
    to be *equal* in all three fields to the canonical member of
    :data:`ARTIFACT_SPECS`, so the path always comes from this module's own
    constants, and is then checked to lie inside :func:`target_root`.

    Args:
        key: An :class:`ArtifactSpec`, or one of its ``key`` values.
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The artifact path.  For the ``"cucumber"`` key this is the
        PrettyReports *directory*, matching :attr:`ArtifactSpec.is_dir`.  No
        filesystem access: the containment check is lexical.

    Raises:
        KeyError: If ``key`` is neither one of the four artifact keys nor one
            of the four canonical specs - a programming error, unlike the
            untrusted input :func:`resolve_artifact` answers with ``None``.
            The message never echoes a caller-supplied ``relpath``.
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


def workers_dir(base: Path | str | None = None) -> Path:
    """Return ``<base>/target/.workers``, the per-worker intermediate directory.

    This is the **shared root** of the intermediates, not a run's own
    directory.  ``app/services/test_run_service.py`` owns the lifecycle
    beneath it: it creates a uniquely named child per run
    (``prepare_workers_dir``), gives each shard an output path inside that
    child, removes what the invocation created (``cleanup_workers_dir``) and
    reclaims what an abandoned run left (``reclaim_workers_root``), so that no
    intermediate JSON is visible to the Jenkins publisher once a command
    returns (AAP 0.4.1).  ``app/cli.py`` drives those service functions at the
    end of the command and takes this path for the one entry of the build
    output its ``--clean`` step must not empty itself.  The leading dot is what
    keeps the whole subtree unreachable through the HTTP artifact route
    (AAP 0.3.1, and :func:`resolve_artifact`).

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

    **Discovery rather than planning, which is why the run path does not use
    it.**  ``app/services/test_run_service.py`` merges the shard paths it
    planned with :func:`worker_result_path`, so a shard that wrote nothing is a
    named absence it can report rather than a directory entry that is simply
    missing - and the merged structure stays deterministic for a fixed set of
    shard inputs, as AAP 0.6 requires, without depending on filesystem
    iteration order at all.  This helper answers the other question - what is
    actually on disk, in a stable order - for a caller that has no plan to
    compare against, and the port's own tests assert the ordering guarantee
    through it.

    Exactly one absence is tolerated, and nothing else: a worker directory that
    is not there, or is not a directory at all, genuinely means *no worker has
    written*.  Every other failure is real, and reporting it as "no results"
    would let a merge publish an empty result set as if the run had produced
    nothing, hiding completed shards behind a success.

    Args:
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        A tuple of the worker result files sorted by filename - which, thanks
        to the zero-padded shard index in :func:`worker_result_path`, is also
        numeric order within a process id.  Empty when the directory is absent,
        is not a directory, or holds no match; an entry that disappears
        mid-scan is skipped, being an absence rather than a failure.

    Raises:
        OSError: Every other failure, unchanged - a denied traversal, an I/O
            error while listing, a symlink loop.  The per-entry test is made
            apart from the listing, so one unreadable entry never reads empty.
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


# No-follow artifact I/O.  The helpers below create directories and open
# files, and delete nothing: AAP 0.4.1 gives emptying the build output to
# ``app/cli.py``, which is why a write truncates in place.


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

#: Whether this platform can restrict an object's mode through its own
#: descriptor.  POSIX-only: Windows expresses permissions as ACLs, where a
#: POSIX mode has no meaning, so the mode policy is simply not applied there
#: (see the permissions section of the module docstring).
_FCHMOD_SUPPORTED: Final[bool] = hasattr(os, "fchmod")

#: Whether a whole publication - staging, rename and scratch removal - can be
#: performed relative to held directory descriptors.  The no-follow primitives
#: are not enough on their own: publishing also renames and removes, and both
#: have to be descriptor-relative for the verified parent to still mean
#: something by the time they run.
_PUBLICATION_SUPPORTED: Final[bool] = bool(
    _NO_FOLLOW_SUPPORTED
    and os.rename in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and os.rmdir in os.supports_dir_fd
)

#: Windows' "this entry is a reparse point" attribute bit, or ``0`` where the
#: platform does not define it.  Junctions, mount points and symbolic links all
#: carry it, which is why the fallback tests it rather than trusting
#: :meth:`~pathlib.Path.is_symlink` - that answers ``False`` for a junction
#: while :func:`os.stat` follows one.
_FILE_ATTRIBUTE_REPARSE_POINT: Final[int] = getattr(
    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
)

#: Directory creation mode for everything this module creates inside the
#: artifact root: owner-only, because the published tree carries failure
#: screenshots and substituted step arguments (CWE-732/CWE-359).  A mode
#: argument is an upper bound the process umask can only narrow, so the
#: descriptor-bound tightening below is what makes the policy hold for an
#: entry an earlier run, or the operator, created more permissively.
ARTIFACT_DIR_MODE: Final[int] = 0o700

#: File creation mode for every artifact this module creates.  ``0o600`` for
#: the same reason :data:`ARTIFACT_DIR_MODE` is ``0o700``.
ARTIFACT_FILE_MODE: Final[int] = 0o600

#: The permission bits cleared from every artifact this module writes and every
#: owned directory it creates or descends on a write - group and other, the
#: ``0o077`` of a ``umask 0077`` policy, applied per object through the
#: object's own descriptor rather than by mutating the process umask.
ARTIFACT_MODE_MASK: Final[int] = 0o077

#: Infix of the staging directory a directory publication builds in.  Combined
#: with a leading dot and the publishing process's id by
#: :func:`_publication_scratch_names`, which is what keeps a partial generation
#: unservable: :func:`resolve_artifact` refuses every dot-prefixed component.
PUBLICATION_STAGING_INFIX: Final[str] = ".staging-"

#: Infix of the directory a publication renames the previous generation aside
#: to while it swaps the new one in.  A tree carrying this infix may be the
#: only complete generation in existence, which is why
#: :meth:`ArtifactDirectoryPublication.restore_superseded` exists and why
#: scratch belonging to another process is never removed.
PUBLICATION_SUPERSEDED_INFIX: Final[str] = ".superseded-"

#: Prefix of the name a scratch directory is renamed to on a platform that
#: cannot remove it without descending it by name - see
#: :func:`_detach_tree_by_name`.  Dot-prefixed, so nothing can serve it, and
#: deliberately *not* one of the two infixes above, so a later publication
#: neither restores it nor tries to dispose of it again; the ``--clean`` step
#: empties the build output and takes it with everything else.
_ABANDONED_PREFIX: Final[str] = ".abandoned-"

#: Infix of the temporary file :func:`publish_artifact_file` writes before it
#: renames the result onto the destination.  Dot-prefixed for the same reason
#: the publication scratch is, and carrying both the process id and a random
#: token so two publications - in one process or two - never share one name.
_PUBLISH_TEMPORARY_INFIX: Final[str] = ".publish-"


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


def _is_reparse_point(info: os.stat_result) -> bool:
    """Return whether ``info`` describes a link of any kind.

    A symbolic link is the case every platform shares.  Windows adds
    *junctions* and mount points, which are directory reparse points that
    :meth:`~pathlib.Path.is_symlink` answers ``False`` for while :func:`os.stat`
    follows them, so a check written only against ``S_ISLNK`` lets a junction
    redirect a write out of the artifact root (CWE-59).  Both extra attributes
    are absent on POSIX, where :func:`getattr` supplies ``0`` and the test
    reduces to the symlink one.

    Args:
        info: A **no-follow** stat result - :func:`os.lstat`, or
            :func:`os.stat` with ``follow_symlinks=False``.  Following the link
            first would report whatever it points at and defeat the test.

    Returns:
        ``True`` for a symbolic link, a junction, a mount point or any other
        reparse point.
    """
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    if _FILE_ATTRIBUTE_REPARSE_POINT and attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    return bool(getattr(info, "st_reparse_tag", 0))


def _tighten_mode(fd: int, *, info: os.stat_result | None = None) -> None:
    """Clear the group and other permission bits of an object being written.

    The mode passed to :func:`os.mkdir` or :func:`os.open` applies only to an
    object that call creates, so an artifact a previous run left at ``0644``, or
    a ``target/`` the operator created at ``0755``, would keep those bits
    (CWE-732/CWE-359).  This is the part of the policy that does not depend on
    who created the entry: the mask is applied to the object **the descriptor
    already holds**, so no pathname is re-resolved and no swap can redirect it.

    Owner bits and the special bits are left as they are - a set-group-id
    build directory stays set-group-id, it simply grants the group nothing.

    Args:
        fd: Descriptor of the artifact or owned directory.  Ownership stays
            with the caller.
        info: An :func:`os.fstat` result for ``fd``, when the caller already
            has one; it is read here otherwise.

    Raises:
        ArtifactPathError: If the bits cannot be cleared and the object is
            still readable by its group or by others - a filesystem that
            ignores :func:`os.chmod` entirely, mounted where the artifacts are
            written.  Reported as a writer failure rather than passed over,
            because the object holds run evidence and is exposed.
        OSError: If the descriptor cannot be stat'ed at all.
    """
    if not _FCHMOD_SUPPORTED:
        # Windows: permissions are ACLs and a POSIX mode has no meaning, so
        # there is nothing to apply and nothing to emulate.
        return
    current = (os.fstat(fd) if info is None else info).st_mode & 0o7777
    wanted = current & ~ARTIFACT_MODE_MASK
    if wanted == current:
        return
    try:
        os.fchmod(fd, wanted)
    except OSError:
        if os.fstat(fd).st_mode & ARTIFACT_MODE_MASK:
            raise ArtifactPathError(
                "the artifact could not be restricted to its owner, and is "
                "readable by the group or by others; refusing to write it"
            ) from None


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
            :exc:`FileExistsError` raised when a non-directory already
            occupies the path.
    """
    _reject_unverifiable(name)
    if create:
        try:
            os.mkdir(name, ARTIFACT_DIR_MODE, dir_fd=parent_fd)
        except FileExistsError:
            # Something is already there.  lstat, not stat, so a symlink - or a
            # junction - is reported as itself rather than as whatever it
            # points at.
            existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if _is_reparse_point(existing):
                raise ArtifactPathError(
                    f"the artifact path component {name!r} is a symbolic link; "
                    "refusing to create or write through it"
                ) from None
            if not stat.S_ISDIR(existing.st_mode):
                raise
    try:
        fd = os.open(
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
    if create:
        # The mode above only applied if this call created the directory, so an
        # owned directory an earlier run or the operator left group-readable is
        # tightened here, through the descriptor just obtained.  Read-side
        # descents deliberately do not: reading an artifact must not alter the
        # workspace, and the process serving it may not even own the tree.
        try:
            _tighten_mode(fd)
        except BaseException:
            os.close(fd)
            raise
    return fd


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
        # The anchor is the operator's own layout rather than part of the
        # artifact tree, so it is created the ordinary way, symlinks followed.
        anchor.mkdir(parents=True, exist_ok=True)
    fd = _descend_chain(anchor, components[:-1], create=create)
    return fd, components[-1]


def _refuse_unsafe_entry(parent_fd: int, name: str) -> None:
    """Refuse a final component that is a link to somewhere else.

    An absent entry is fine - it is about to be created - and so is a plain
    regular file with one link, because writing it in place overwrites what the
    previous run left.  The two refusals are the two ways the entry can be
    somewhere else as well: a **symbolic** link, because following it would
    write outside the artifact root, and a **hard** link - a regular file whose
    link count exceeds one - because the entry *is* that other file, with no
    symlink in the path for a symlink check to find.

    The test is made on the name, :func:`os.stat` at ``follow_symlinks=False``
    relative to the verified parent, so it settles what is there *now*.  It is
    what a caller holding only the returned path gets; one that goes on to open
    the entry re-establishes both properties on the descriptor it obtained
    (:func:`_verify_open_artifact`).

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


def _bound_identity(path: Path, name: str) -> tuple[int, int]:
    """Return the device and inode of ``path``, refusing a link or an unbindable
    object.

    The fallback branch's unit of binding.  It reads the entry **no-follow**, so
    a symbolic link or a junction is reported as itself, and it refuses a
    filesystem that cannot identify its objects at all rather than proceeding
    on a name alone.

    Args:
        path: The entry to identify.
        name: Its final component, for the message.

    Returns:
        ``(st_dev, st_ino)`` - the pair that says *which object* this is.

    Raises:
        ArtifactPathError: If the entry is a reparse point of any kind, or if
            the filesystem reports no inode number, which leaves nothing to
            bind and is therefore refused (fail closed).
        OSError: If the entry cannot be examined - :exc:`FileNotFoundError` for
            an absent one, unchanged, so a caller can tell absence from
            refusal.
    """
    info = os.lstat(path)
    if _is_reparse_point(info):
        raise ArtifactPathError(
            f"the artifact path component {name!r} is a symbolic link; "
            "refusing to create, write or read through it"
        )
    if not info.st_ino:
        raise ArtifactPathError(
            f"the filesystem reports no identity for the artifact path "
            f"component {name!r}, so it cannot be verified; refusing to use it"
        )
    return (info.st_dev, info.st_ino)


class _FallbackAnchor:
    """Object-bound verification for a platform without the no-follow
    primitives.

    Where ``O_NOFOLLOW`` and ``dir_fd`` are unavailable a parent cannot be held
    open, so this binds the owned directory chain by **object identity**
    instead: each component is read no-follow, refused if it is a reparse point
    of any kind, and its ``(st_dev, st_ino)`` recorded.  :meth:`verify` then
    re-establishes that snapshot immediately before each destructive step, so a
    component swapped out between the walk and the step is refused rather than
    written through (CWE-367/CWE-59).

    It is not equivalent to a held descriptor and does not pretend to be: it
    detects a swap rather than preventing one.  What makes the detection
    sufficient is ordering - every caller verifies *before* the step that
    destroys or publishes anything, and the write path truncates last - and
    failing closed, both here and in :func:`_bound_identity`.

    Attributes:
        path: The artifact path this anchor was built for.
        anchor: Its trusted anchor directory.
        components: The owned components beneath the anchor, in order.
    """

    __slots__ = ("anchor", "components", "path", "_bound")

    def __init__(self, path: Path) -> None:
        """Split ``path`` at the trusted anchor and reject unverifiable names.

        Args:
            path: The artifact file or directory.

        Raises:
            ArtifactPathError: If an owned component is ``.`` or ``..``.
        """
        self.path = path
        self.anchor, self.components = _trusted_anchor(path)
        for name in self.components:
            _reject_unverifiable(name)
        self._bound: tuple[tuple[Path, str, tuple[int, int]], ...] = ()

    @property
    def final(self) -> str:
        """The final component of the path, which names the entry itself.

        Raises:
            ArtifactPathError: If the path names no entry at all - a bare
                filesystem root, or ``.``.
        """
        if not self.components:
            raise ArtifactPathError(
                "an artifact path must name an entry beneath a directory"
            )
        return self.components[-1]

    def _directory_components(self, *, entry_is_directory: bool) -> tuple[str, ...]:
        """Return the components that are directories on the way to the entry."""
        if entry_is_directory:
            return self.components
        return self.components[:-1]

    def bind(self, *, entry_is_directory: bool = False) -> None:
        """Record the identity of every owned directory on the way down.

        Args:
            entry_is_directory: Whether the final component is itself one of the
                directories to bind, which it is for :func:`ensure_dir` and not
                for a file.

        Raises:
            ArtifactPathError: For a component that is a reparse point or has
                no identity.
            OSError: If a component is absent or cannot be examined.
        """
        bound: list[tuple[Path, str, tuple[int, int]]] = []
        current = self.anchor
        for name in self._directory_components(entry_is_directory=entry_is_directory):
            current = current / name
            bound.append((current, name, _bound_identity(current, name)))
        self._bound = tuple(bound)

    def prepare(self, *, entry_is_directory: bool = False) -> None:
        """Create the owned directories with the mode policy, then bind them.

        Each component is created individually rather than through
        ``mkdir(parents=True)``, so :data:`ARTIFACT_DIR_MODE` applies to every
        one of them, and an existing component is refused if it is a reparse
        point before anything is created beneath it.

        Args:
            entry_is_directory: As :meth:`bind`.

        Raises:
            ArtifactPathError: For a component that is a reparse point, has no
                identity, or is not a directory.
            OSError: If a directory cannot be created.
        """
        self.anchor.mkdir(parents=True, exist_ok=True)
        current = self.anchor
        for name in self._directory_components(entry_is_directory=entry_is_directory):
            current = current / name
            try:
                current.mkdir(ARTIFACT_DIR_MODE)
            except FileExistsError:
                existing = os.lstat(current)
                if _is_reparse_point(existing):
                    raise ArtifactPathError(
                        f"the artifact path component {name!r} is a symbolic "
                        "link; refusing to create or write through it"
                    ) from None
                if not stat.S_ISDIR(existing.st_mode):
                    raise
                _tighten_by_name(current)
        self.bind(entry_is_directory=entry_is_directory)

    def verify(self) -> None:
        """Re-establish the bound identities, refusing any that changed.

        Raises:
            ArtifactPathError: If a component is now a reparse point, is a
                different object than the one bound, or has gone.
            OSError: If a component cannot be examined.
        """
        for path, name, identity in self._bound:
            try:
                current = _bound_identity(path, name)
            except FileNotFoundError:
                raise ArtifactPathError(
                    f"the artifact path component {name!r} disappeared while "
                    "it was being used; refusing to continue"
                ) from None
            if current != identity:
                raise ArtifactPathError(
                    f"the artifact path component {name!r} changed while it "
                    "was being used; refusing to write or read through it"
                )

    def verify_entry(self, entry: Path | None = None) -> None:
        """Refuse a final entry that is a link to, or is, a file elsewhere.

        Args:
            entry: The entry to test; defaults to this anchor's own path.  A
                publication passes a sibling of it.

        Raises:
            ArtifactPathError: If the entry exists and is a reparse point, or
                is a regular file with more than one hard link, or - for this
                anchor's own path - if the path names no entry at all, which is
                the refusal the primitive branch makes in
                :func:`_verified_parent_fd`; the two branches answer the same
                shapes the same way.
            OSError: If it cannot be examined for any other reason.
        """
        self.verify()
        if entry is None and not self.components:
            # The same refusal, in the same words, as the primitive branch's
            # _verified_parent_fd: a path with no final component has no entry
            # to verify under a parent.
            raise ArtifactPathError(
                "an artifact path must name an entry beneath a directory"
            )
        target = self.path if entry is None else entry
        try:
            existing = os.lstat(target)
        except FileNotFoundError:
            return
        if _is_reparse_point(existing):
            raise ArtifactPathError(
                f"the artifact entry {target.name!r} is a symbolic link; "
                "refusing to write or read through it"
            )
        if stat.S_ISREG(existing.st_mode) and existing.st_nlink > 1:
            raise ArtifactPathError(
                f"the artifact entry {target.name!r} has more than one hard "
                "link, so it is also a file elsewhere; refusing to write or "
                "read through it"
            )


def _tighten_by_name(path: Path) -> None:
    """Apply the mode policy to an existing directory named by ``path``.

    The fallback branch's form of :func:`_tighten_mode`, for a platform that
    cannot hold the directory open while it is tightened.  It is opened for the
    duration of the call so the mask still lands on an object rather than on a
    name, and a platform without :func:`os.fchmod` is a no-op exactly as it is
    there.

    Args:
        path: An owned directory that already existed.

    Raises:
        ArtifactPathError: As :func:`_tighten_mode`.
    """
    if not _FCHMOD_SUPPORTED:
        return
    fd = os.open(path, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
    try:
        _tighten_mode(fd)
    finally:
        os.close(fd)


def _verify_open_artifact(fd: int, *, action: str) -> os.stat_result:
    """Interrogate an open descriptor and refuse anything but a lone regular file.

    The check both sides make on the object they have actually opened rather
    than on the name they opened it by, so no later change to the pathname can
    invalidate it.  Two conditions:

    * It must be a **regular file**: a FIFO would block a writer for ever and
      hand a reader bytes no run ever produced.
    * It must have **exactly one link**.  ``O_NOFOLLOW`` refuses a *symbolic*
      link and nothing refuses a **hard** one, yet an entry hard-linked to a
      file outside the artifact root *is* that file.  A writer creates its own
      output, so one link is what a genuine artifact has; a platform reporting
      no link count (``0``) is not second-guessed.

    Args:
        fd: An open descriptor; ownership stays with the caller.
        action: ``"read"`` or ``"write"``, for the message only.

    Returns:
        The :func:`os.fstat` result, so the caller need not stat twice.

    Raises:
        ArtifactPathError: For anything but a regular file with one link.
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
    anchor: "_FallbackAnchor | None" = None,
) -> IO[Any]:
    """Verify a freshly opened write descriptor, empty it, and wrap it.

    The order is the whole point, and why :func:`open_artifact_write` opens
    without ``O_TRUNC``: verify what was opened, *then* discard its contents.
    Truncating during the open would destroy the wrong file first.

    Args:
        fd: The descriptor just opened for writing.  This function takes
            ownership of it and closes it on every error path.
        destination: The name it was opened by, for the fallback comparison.
        binary: Whether to hand back a binary stream.
        encoding: Text encoding, ignored when ``binary`` is set.
        newline: Newline translation, ignored when ``binary`` is set.
        verify_by_name: ``True`` on the fallback path, where the open could not
            refuse a symlink and the opened object is therefore compared with
            what the name reports now; ``False`` where ``O_NOFOLLOW`` under a
            held directory descriptor already established it.
        anchor: The fallback branch's bound directory chain, re-established
            here immediately before the truncation; ``None`` where a held
            descriptor made the binding unnecessary.

    Returns:
        An open file object positioned at the start of an empty file.

    Raises:
        ArtifactPathError: If the descriptor does not hold a lone regular file,
            if its mode cannot be restricted to its owner, or - on the fallback
            path - if it no longer holds the object the name refers to or a
            bound directory changed.  Nothing has been written or truncated
            when this is raised.
        OSError: If the descriptor cannot be stat'ed, truncated or wrapped.
    """
    try:
        info = _verify_open_artifact(fd, action="write")
        if verify_by_name:
            _verify_opened_by_name(destination, fd, info)
        if anchor is not None:
            anchor.verify()
        # The creation mode applied only if this open created the file, so an
        # artifact an earlier run left group-readable is tightened here -
        # before it is truncated and written, and through its own descriptor.
        _tighten_mode(fd, info=info)
        # Only now, with the object established, is the previous run's content
        # discarded - in place, because this module removes nothing but its own
        # publication scratch.
        os.ftruncate(fd, 0)
        if binary:
            return os.fdopen(fd, "wb")
        return os.fdopen(fd, "w", encoding=encoding, newline=newline)
    except BaseException:
        os.close(fd)
        raise


def _regular_file_stream(
    fd: int,
    source: Path | None = None,
    *,
    verify_by_name: bool = False,
    anchor: "_FallbackAnchor | None" = None,
) -> BinaryIO:
    """Wrap an open descriptor as a binary stream, if it holds a regular file.

    The :func:`os.fstat` in :func:`_verify_open_artifact` is the load-bearing
    step of the read side: it interrogates the **object the descriptor already
    holds**, so nothing that happens to the pathname afterwards can invalidate
    its answer.

    Args:
        fd: An open descriptor this function takes ownership of.
        source: The name the descriptor was opened by, required when
            ``verify_by_name`` is set.
        verify_by_name: ``True`` on the fallback path, where the open could not
            refuse a symlink, so the opened object is compared with what the
            name reports now - see :func:`_verify_opened_by_name`.
        anchor: The fallback branch's bound directory chain, re-established
            here **after** the open and before a single byte can be read.
            Without it the name comparison alone is not enough: a directory
            swapped before the open makes the name and the opened object agree
            on the *substituted* file, so both sides of that comparison are the
            attacker's.  ``None`` where a held descriptor made the binding
            unnecessary.

    Returns:
        A buffered binary stream over ``fd``.

    Raises:
        ArtifactPathError: If the descriptor holds anything but a regular file
            with a single link - a directory, a FIFO, a device, a hard-linked
            entry - or, on the fallback path, if a bound directory changed or
            the descriptor no longer holds the object the name refers to.  The
            descriptor is closed first, so nothing is read from it.
        OSError: If the descriptor cannot be stat'ed or wrapped.  It is closed
            on every error path, so no descriptor is ever leaked.
    """
    try:
        info = _verify_open_artifact(fd, action="read")
        # Chain first, name second, and both before the stream is handed back.
        # The order matters: a *sustained* swap is caught by the chain (the
        # bound identity no longer matches), and a swap undone before the
        # checks is caught by the name comparison (the restored name resolves
        # to the original object, which is not the one held open).
        if anchor is not None:
            anchor.verify()
        if verify_by_name and source is not None:
            _verify_opened_by_name(source, fd, info)
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def ensure_dir(path: Path | str) -> Path:
    """Create ``path`` as a directory, including missing parents, and return it.

    Idempotent: an existing directory is left untouched.  For a caller that
    needs the **directory itself** rather than a stream or a publication -
    ``app/services/test_run_service.py`` creates this run's per-worker
    directory with it.  A caller that is about to write a file takes its stream
    from :func:`open_artifact_write` or :func:`publish_artifact_file`, and one
    publishing a whole tree from :func:`begin_directory_publication`; all three
    create and verify their own directories, so they need no separate call
    here.

    Every component from the ``target`` component inward is created *and then
    opened* under the descriptor of its verified parent, with ``O_NOFOLLOW``,
    so a symlink standing in for ``target/`` or for any directory below it is
    refused instead of traversed - the case a ``--no-clean`` run, or a clean
    that failed part-way, leaves behind.  Each one is created
    :data:`ARTIFACT_DIR_MODE` and an existing one is tightened through its own
    descriptor, per the permissions section of the module docstring.
    Components above the artifact root are the operator's own layout and are
    created the ordinary way; see the trusted-anchor rule there.

    Args:
        path: Directory to create.

    Returns:
        The directory as a :class:`~pathlib.Path`, unchanged, so a caller can
        pass the result straight on.

    Raises:
        ArtifactPathError: If a component from ``target`` inward is a symbolic
            link or is not a directory.  It is an :exc:`OSError`, so a caller
            that already handles one needs no change.
        OSError: If the directory cannot be created.  Not swallowed: a writer
            that cannot create its output directory must fail the run per the
            exit contract in AAP 0.4.1.
    """
    directory = Path(path)
    anchor, components = _trusted_anchor(directory)
    if not components:
        directory.mkdir(parents=True, exist_ok=True)
        return directory
    if not _NO_FOLLOW_SUPPORTED:
        # Object-bound: each component is created with the mode policy, an
        # existing one is refused if it is a reparse point and tightened if it
        # is not, and the chain is left bound for the caller's next step.
        _FallbackAnchor(directory).prepare(entry_is_directory=True)
        return directory
    anchor.mkdir(parents=True, exist_ok=True)
    fd = _descend_chain(anchor, components, create=True)
    os.close(fd)
    return directory


def ensure_parent(path: Path | str) -> Path:
    """Create the parent directory of ``path`` and return ``path`` itself.

    For a caller that needs an artifact's parent directory to exist without
    opening the artifact - so that nothing fails merely because ``target/`` was
    emptied by the ``--clean`` step.  No production module calls it: the
    writers that used to take their parent from here now take their stream from
    one of the write entry points instead, and what is left of its use is the
    port's own tests, which prepare an artifact tree by the same route
    production code creates one.  **It is not the write route.**  The three
    write entry points named in the module docstring create and verify the
    parent themselves and then open or publish the artifact under the same held
    descriptor; a caller that calls this and then names the path again in a
    plain ``open`` has reopened it, which is the window those entry points
    exist to close (CWE-367/CWE-59).

    The parent is created and verified exactly as :func:`ensure_dir` does it,
    and the **final entry is checked too**: one that already exists as a
    symbolic link, or as a regular file with more than one hard link, is
    refused - both are ways for the destination to be a file somewhere else as
    well (:func:`_refuse_unsafe_entry`).  That check is what a caller holding
    only the returned path gets; it settles what is there *now* and cannot
    settle what is there when that caller opens it.

    Args:
        path: File whose parent directory must exist.

    Returns:
        ``path`` as a :class:`~pathlib.Path`, unchanged, so the call can wrap an
        expression.

    Raises:
        ArtifactPathError: If a component from ``target`` inward is a symbolic
            link or is not a directory, or if the final entry exists as a
            symbolic link or a hard-linked regular file.
    """
    destination = Path(path)
    if not _NO_FOLLOW_SUPPORTED:
        anchor = _FallbackAnchor(destination)
        anchor.prepare()
        anchor.verify_entry()
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

    Truncating in place, rather than writing a temporary file and renaming it
    over the destination, is what distinguishes this from
    :func:`publish_artifact_file`, and the choice belongs to the caller: with
    the destination established as a lone regular file inside a verified
    directory, a rename adds **atomicity** rather than safety.  A machine-read
    artifact that is rewritten between runs takes this one; an artifact a
    reader may be looking at while it is rewritten takes the publishing one.

    Args:
        path: Artifact file to write.
        binary: ``True`` for a binary stream, which ignores the two below.
        encoding: Text encoding; ignored when ``binary`` is set.
        newline: Newline translation; the default writes ``\n`` unchanged on
            every platform.  Ignored when ``binary`` is set.

    Returns:
        An open file object, owned by the caller, who closes it.

    Raises:
        ArtifactPathError: If a component from ``target`` inward or the
            destination itself is a symbolic link, is not a regular file, has
            more than one link, or changed while opened.  Nothing is written.
        OSError: If the parent cannot be created or the file cannot be opened.
    """
    destination = Path(path)
    # O_TRUNC is deliberately absent from both branches: the file is opened,
    # the object that opened is verified, and only then is it truncated.  A
    # truncation performed by the open itself would have destroyed the wrong
    # file before any check could refuse it.
    flags = os.O_WRONLY | os.O_CREAT | _O_NOFOLLOW | _O_NONBLOCK

    if not _NO_FOLLOW_SUPPORTED:
        write_anchor = _FallbackAnchor(destination)
        write_anchor.prepare()
        write_anchor.verify_entry()
        try:
            handle_fd = os.open(destination, flags, ARTIFACT_FILE_MODE)
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
            anchor=write_anchor,
        )
        return handle

    fd, final = _verified_parent_fd(destination, create=True)
    try:
        _refuse_unsafe_entry(fd, final)
        try:
            handle_fd = os.open(final, flags, ARTIFACT_FILE_MODE, dir_fd=fd)
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


def _exclusive_write_stream(
    fd: int,
    *,
    binary: bool,
    encoding: str,
    newline: str,
) -> IO[Any]:
    """Verify a descriptor this module just created exclusively, and wrap it.

    The counterpart of :func:`_truncated_write_stream` for a file opened
    ``O_CREAT|O_EXCL``: there is nothing to truncate, because the open would
    have failed had anything - a file, a symbolic link, a junction - already
    been there under that name, and the creation mode therefore applied.

    Args:
        fd: The descriptor just created.  This function takes ownership and
            closes it on every error path.
        binary: Whether to hand back a binary stream.
        encoding: Text encoding, ignored when ``binary`` is set.
        newline: Newline translation, ignored when ``binary`` is set.

    Returns:
        An open file object positioned at the start of an empty file.

    Raises:
        ArtifactPathError: If the descriptor does not hold a lone regular file,
            or its mode cannot be restricted to its owner.
        OSError: If the descriptor cannot be stat'ed or wrapped.
    """
    try:
        info = _verify_open_artifact(fd, action="write")
        _tighten_mode(fd, info=info)
        if binary:
            return os.fdopen(fd, "wb")
        return os.fdopen(fd, "w", encoding=encoding, newline=newline)
    except BaseException:
        os.close(fd)
        raise


def _publish_temporary_name(name: str) -> str:
    """Return the name of the temporary a publication of ``name`` writes first.

    Dot-prefixed, so :func:`resolve_artifact` cannot serve it while it exists;
    derived from the destination, so it is recognisable in a directory listing;
    and carrying both the process id and a random token, so two publications -
    in one process or in two - never claim one temporary.

    Args:
        name: Final component of the destination.

    Returns:
        The temporary's final component, of the form
        ``.<name>.publish-<pid>-<token>.partial``.
    """
    token = os.urandom(8).hex()
    return f".{name}{_PUBLISH_TEMPORARY_INFIX}{os.getpid()}-{token}.partial"


def _discard_temporary(
    name: str,
    *,
    parent_fd: int | None,
    path: Path,
    anchor: "_FallbackAnchor | None" = None,
) -> None:
    """Remove a publication temporary, never raising.

    A publication that is already failing must report the fault that caused it,
    so a removal that itself fails is passed over: what is left behind is a
    dot-prefixed file no request can reach, and the ``--clean`` step of the next
    ordinary run removes it.

    Args:
        name: The temporary's final component, for the descriptor-relative
            removal.
        parent_fd: Descriptor of its verified parent, or ``None`` on the
            fallback branch.
        path: The temporary's full path, used by the fallback branch.
        anchor: The fallback branch's bound chain, re-established before the
            unlink so that a substituted parent leaves the temporary behind
            instead of taking an unrelated file with that name.  ``unlink``
            follows no link, so the entry named is the only thing at risk.
    """
    try:
        if parent_fd is None:
            if anchor is not None:
                anchor.verify()
            os.unlink(path)
        else:
            os.unlink(name, dir_fd=parent_fd)
    except OSError:
        # Nothing to do and nothing to say from a module with no logger: the
        # caller's own exception is the one worth reporting.
        pass


@contextmanager
def publish_artifact_file(
    path: Path | str,
    *,
    binary: bool = False,
    encoding: str = "utf-8",
    newline: str = "\n",
) -> Iterator[IO[Any]]:
    """Write an artifact into a temporary and rename it onto ``path`` at the end.

    The publication form of :func:`open_artifact_write`, for an artifact a
    reader may be looking at while it is rewritten - ``app/reporting/
    html_report.py`` publishes ``target/cucumber-reports.html`` through it.  The
    caller writes to the stream this yields; when its block completes, the
    bytes are flushed, :func:`os.fsync`'ed and the temporary is renamed onto the
    destination in one indivisible step, so a reader, an archiver or the
    artifact route sees the previous complete artifact or the new one and never
    a document truncated where a write failed.

    **The whole sequence runs under one verified directory descriptor.**  The
    parent is created and verified as :func:`ensure_parent` does it, and the
    temporary is then created, written and renamed *relative to that
    descriptor* - so the directory the rename lands in is the directory that
    was verified, rather than a pathname that may have become a symbolic link
    or a junction in between (CWE-59/CWE-367).  The temporary is created
    ``O_CREAT|O_EXCL``, which is what makes its own name unswappable: a link
    planted under it fails the creation instead of being written through.  On a
    platform without descriptor-relative renaming the same sequence runs
    against the bound directory chain described in the module docstring, whose
    identities are re-established immediately before the rename.

    The temporary is removed on **every** path out of this function except a
    successful rename - the failed rename included - and its name is
    dot-prefixed, so it is unservable for the moment it exists.

    Args:
        path: Artifact file to publish.
        binary: ``True`` for a binary stream, which ignores ``encoding`` and
            ``newline`` as the builtin does.
        encoding: Text encoding; ignored when ``binary`` is set.
        newline: Newline translation; ignored when ``binary`` is set.  The
            default writes ``\\n`` unchanged on every platform, which the
            artifacts require.

    Yields:
        An open file object for the caller to write the whole artifact to.  It
        is closed here, and must not be held on to after the block ends; a
        caller that closes it itself is tolerated - the publication then loses
        only the device sync, which is not worth failing a complete artifact
        over.

    Raises:
        ArtifactPathError: If a component from ``target`` inward is a symbolic
            link or a junction, if the destination already exists as a link or
            as a hard-linked file, if the temporary cannot be restricted to its
            owner, or - on the fallback branch - if a bound directory changed
            while the artifact was being written.  The destination still holds
            the previous complete artifact in every case.
        OSError: If the parent cannot be created, or the temporary cannot be
            created, written, synced or renamed.  The destination is again
            untouched, because it is only ever reached by the rename.
    """
    destination = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_NONBLOCK

    if not _PUBLICATION_SUPPORTED:
        anchor = _FallbackAnchor(destination)
        anchor.prepare()
        anchor.verify_entry()
        temporary_path = destination.with_name(
            _publish_temporary_name(destination.name)
        )
        handle_fd = os.open(temporary_path, flags, ARTIFACT_FILE_MODE)
        published = False
        try:
            stream = _exclusive_write_stream(
                handle_fd, binary=binary, encoding=encoding, newline=newline
            )
            try:
                yield stream
                if not stream.closed:
                    # A caller that closed the stream itself has already
                    # flushed it through the interpreter; only the device sync
                    # is lost, and refusing the publication over it would fail
                    # a run for a complete artifact.
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                if not stream.closed:
                    stream.close()
            # The destination's directory chain is re-established immediately
            # before the rename, so a parent swapped while the artifact was
            # being written cannot receive it.
            anchor.verify()
            anchor.verify_entry()
            os.replace(temporary_path, destination)
            published = True
        finally:
            if not published:
                _discard_temporary(
                    temporary_path.name,
                    parent_fd=None,
                    path=temporary_path,
                    anchor=anchor,
                )
        return

    parent_fd, final = _verified_parent_fd(destination, create=True)
    try:
        _refuse_unsafe_entry(parent_fd, final)
        temporary = _publish_temporary_name(final)
        handle_fd = os.open(temporary, flags, ARTIFACT_FILE_MODE, dir_fd=parent_fd)
        published = False
        try:
            stream = _exclusive_write_stream(
                handle_fd, binary=binary, encoding=encoding, newline=newline
            )
            try:
                yield stream
                if not stream.closed:
                    # A caller that closed the stream itself has already
                    # flushed it through the interpreter; only the device sync
                    # is lost, and refusing the publication over it would fail
                    # a run for a complete artifact.
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                if not stream.closed:
                    stream.close()
            _refuse_unsafe_entry(parent_fd, final)
            os.rename(temporary, final, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            published = True
        finally:
            if not published:
                _discard_temporary(
                    temporary,
                    parent_fd=parent_fd,
                    path=destination.with_name(temporary),
                )
    finally:
        os.close(parent_fd)


def open_artifact_read(path: Path | str) -> BinaryIO:
    """Open an existing artifact file for reading, with no symlink in the path.

    The binary counterpart of :func:`open_artifact_write`: every component from
    ``target`` inward is opened ``O_NOFOLLOW`` under its verified parent, and
    the descriptor is then :func:`os.fstat`-checked to hold a regular file with
    a single link - refusing a directory, a FIFO, a device, and an entry
    hard-linked outside the artifact root, which no symlink check can see.
    Checking the descriptor rather than the name establishes what was actually
    opened.  Nothing is created: a missing file is reported, not filled in.

    Args:
        path: Artifact file to read.

    Returns:
        An open buffered binary stream, owned by the caller.  Decode it, or use
        :func:`read_artifact_text`, which does that in one step.

    Raises:
        ArtifactPathError: If a component from ``target`` inward or the entry
            itself is a symbolic link, is not a regular file - a directory
            included - has more than one link, or changed while being opened.
        FileNotFoundError: If the entry or one of its directories is absent.
        PermissionError: If it cannot be opened.  Both propagate as themselves.
        OSError: Any other platform failure.
    """
    source = Path(path)
    if not _NO_FOLLOW_SUPPORTED:
        read_anchor = _FallbackAnchor(source)
        read_anchor.bind()
        try:
            handle_fd = os.open(source, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK)
        except OSError as error:
            _refuse_open_failure(error, source.name, action="read")
            raise
        return _regular_file_stream(
            handle_fd, source, verify_by_name=True, anchor=read_anchor
        )

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
# Directory publication.
#
# The fourth artifact is a *directory*, so it cannot be published by one
# atomic write: the whole tree is built in a dot-prefixed staging sibling and
# swapped into place by rename.  That makes this the one place in the module
# that removes anything - its own staging and renamed-aside scratch, through
# the descriptor it verified, and nothing else.  See the module docstring.
# --------------------------------------------------------------------------- #


#: Chunk size :meth:`ArtifactDirectoryPublication.copy_in` reads an asset in.
#: The vendored set is a handful of minified files and two icon fonts, the
#: largest a few hundred kilobytes, so this bounds the copy's memory without
#: making it slow.
_COPY_CHUNK_BYTES: Final[int] = 256 * 1024


def _relative_parts(relative_name: str) -> tuple[str, ...]:
    """Split a staging-relative name into components, refusing an unusable one.

    The names come from the writer's own page and asset inventory rather than
    from a request, so this is a guard against a construction mistake rather
    than against an attacker - but it is the guard that keeps the publication
    inside its staging tree, so it refuses rather than normalises.

    Args:
        relative_name: Slash-separated path inside the staging tree.  A
            backslash is read as a separator too, so a Windows-style spelling
            cannot smuggle a component through as part of a name.

    Returns:
        The path components, at least one.

    Raises:
        ArtifactPathError: For an empty name, an absolute one, a drive letter,
            a doubled separator, or a ``.`` or ``..`` component.
    """
    normalized = relative_name.replace("\\", "/")
    if not normalized or normalized.startswith("/"):
        raise ArtifactPathError(
            "a report tree entry must be named by a relative path"
        )
    if len(normalized) >= 2 and normalized[1] == ":":
        raise ArtifactPathError(
            "a report tree entry must not be named by a drive-qualified path"
        )
    parts = tuple(normalized.split("/"))
    for part in parts:
        if not part:
            raise ArtifactPathError(
                "a report tree entry name must not contain an empty component"
            )
        _reject_unverifiable(part)
    return parts


class PublicationScratch(NamedTuple):
    """One scratch directory found beside a published tree.

    A run killed between the two renames of a publication leaves scratch
    behind, and what should happen to it depends on which kind it is: a
    renamed-aside tree may be the only complete generation in existence, and
    scratch carrying another process's id may belong to a publication that is
    still running.  These fields are what let a caller tell those apart
    without parsing a name itself.

    Attributes:
        name: The entry's final component.
        path: Its full path, for a log record.
        is_own: Whether it is one of the two names *this* publication uses, so
            a previous run of this same process left it and it is safe to
            remove.
        is_superseded: Whether it is a renamed-aside tree rather than a staging
            tree.
        is_dir: Whether it is a directory - a stray file under a scratch name
            is not a tree to restore.
        modified_at: Its modification time, so the newest of several
            renamed-aside copies can be chosen deterministically.
    """

    name: str
    path: Path
    is_own: bool
    is_superseded: bool
    is_dir: bool
    modified_at: float


def _remove_tree_relative(parent_fd: int, name: str) -> None:
    """Remove ``name`` and everything under it, relative to ``parent_fd``.

    Every step is descriptor-relative and no-follow, which is what keeps a
    removal inside the tree it was asked to remove: a directory is descended
    only through a descriptor opened ``O_NOFOLLOW`` on the entry itself, so a
    symbolic link or a junction planted inside the scratch is **unlinked**
    rather than followed, and nothing outside can be reached (CWE-59/CWE-22).

    Args:
        parent_fd: Descriptor of the verified directory the entry sits in.
        name: The entry's final component.

    Raises:
        OSError: If an entry cannot be examined or removed.
    """
    _reject_unverifiable(name)
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISDIR(info.st_mode) and not _is_reparse_point(info):
        fd = os.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=parent_fd)
        try:
            for entry in os.listdir(fd):
                _remove_tree_relative(fd, entry)
        finally:
            os.close(fd)
        os.rmdir(name, dir_fd=parent_fd)
        return
    os.unlink(name, dir_fd=parent_fd)


def _detach_tree_by_name(path: Path) -> bool:
    """Dispose of a scratch entry on the fallback branch **without descending it**.

    The fallback cannot hold a directory open while it lists it, so a recursive
    removal by name is a race with a consequence too large to accept: a
    directory substituted between the stat that approved it and the listing
    that walks it sends the removal into whatever the substitute points at, and
    the removal then deletes content outside the artifact tree
    (CWE-59/CWE-22).  Re-stat'ing the name in between narrows that window
    rather than closing it, so this branch does not walk a directory at all.
    It does the two things it can do without following anything:

    * an entry that is **not a directory** - a file, a symbolic link, a
      junction, a FIFO - is :func:`os.unlink`-ed, which removes the entry
      itself and never what it points at; and
    * a directory is :func:`os.rmdir`-ed, which succeeds only when it is empty
      and likewise follows nothing.  A directory with anything in it is
      **renamed aside** instead, to a dot-prefixed name no request can reach
      (:func:`resolve_artifact` refuses every dot-prefixed component), and left
      for the ``--clean`` step that empties the build output on the next
      ordinary run.

    So the worst case on this branch is a scratch directory that outlives the
    call under a name nothing can serve, rather than a deletion that reaches
    outside the tree.  The descriptor-capable branch has no such trade-off and
    removes the tree outright: see :func:`_remove_tree_relative`.

    Args:
        path: The scratch entry to dispose of.

    Returns:
        ``True`` when the entry was removed, ``False`` when it was renamed
        aside because it still had contents.

    Raises:
        OSError: If the entry cannot be examined, removed or renamed.
    """
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or _is_reparse_point(info):
        os.unlink(path)
        return True
    try:
        os.rmdir(path)
        return True
    except OSError as error:
        if error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
            raise
    abandoned = path.with_name(
        f"{_ABANDONED_PREFIX}{os.getpid()}-{os.urandom(4).hex()}.{path.name}"
    )
    os.rename(path, abandoned)
    return False


class ArtifactDirectoryPublication:
    """A staged, descriptor-bound publication of one directory artifact.

    ``app/reporting/pretty_reports.py`` publishes the ``target/cucumber`` tree
    through this: the pages and assets are written into a staging sibling, the
    complete tree is swapped into place by rename, and the scratch is removed -
    all of it relative to the directory descriptor this object verified when it
    was created, so no step re-resolves a pathname that could have become a
    symbolic link or a junction in between (CWE-59/CWE-367).

    The staging and renamed-aside directories are siblings of the published
    tree rather than children of a temporary directory, and that is a
    correctness requirement: a rename is atomic - indeed on most platforms only
    possible - within one filesystem, and a sibling is on the filesystem the
    published tree is on, wherever the build output happens to be mounted.
    Both names are dot-prefixed, so :func:`resolve_artifact` cannot serve a
    partial generation even while it exists, and both carry the publishing
    process's id, which is what :attr:`PublicationScratch.is_own` reports.

    **What it decides, and what the caller decides.**  This object owns the
    filesystem operations and refuses the unsafe ones - a reparse point where
    the published tree belongs, a scratch name it does not recognise, a
    traversal component it cannot verify.  Which scratch to restore, which to
    leave for another process, what to log and what to do after a failure stay
    with the writer, which is why the query methods return data rather than
    acting on it.

    Attributes:
        final: The published tree's directory.
        staging: The directory the new generation is built in.
        superseded: Where the previous generation is renamed while the swap
            happens.
    """

    __slots__ = (
        "final",
        "staging",
        "superseded",
        "_anchor",
        "_final_name",
        "_moved_aside",
        "_parent_fd",
        "_published",
        "_staging_anchor",
        "_staging_fd",
    )

    def __init__(self, final: Path | str, *, pid: int | None = None) -> None:
        """Verify the published tree's parent and hold it open.

        Args:
            final: The published tree's directory.  Its parent is created if it
                is absent, exactly as :func:`ensure_parent` would create it.
            pid: Process id to build the scratch names from; defaults to
                :func:`os.getpid`.  Passed explicitly by a test asserting the
                recovery rules for another process's leavings.

        Raises:
            ArtifactPathError: If a component from ``target`` inward is a
                symbolic link or a junction, or if the path names no entry.
            OSError: If the parent cannot be created or opened.
        """
        self.final = Path(final)
        resolved_pid = os.getpid() if pid is None else pid
        self.staging = self.final.with_name(
            f".{self.final.name}{PUBLICATION_STAGING_INFIX}{resolved_pid}"
        )
        self.superseded = self.final.with_name(
            f".{self.final.name}{PUBLICATION_SUPERSEDED_INFIX}{resolved_pid}"
        )
        self._published = False
        self._moved_aside = False
        self._staging_fd: int | None = None
        self._parent_fd: int | None = None
        self._anchor: _FallbackAnchor | None = None
        self._staging_anchor: _FallbackAnchor | None = None
        if _PUBLICATION_SUPPORTED:
            fd, name = _verified_parent_fd(self.final, create=True)
            self._parent_fd = fd
            self._final_name = name
        else:
            anchor = _FallbackAnchor(self.final)
            anchor.prepare()
            self._anchor = anchor
            self._final_name = anchor.final

    # -- state --------------------------------------------------------------

    @property
    def published(self) -> bool:
        """Whether :meth:`publish` has completed the swap."""
        return self._published

    @property
    def moved_aside(self) -> bool:
        """Whether a previous generation was renamed to :attr:`superseded`.

        A failure after that rename is the one case in which the renamed-aside
        copy is the only complete generation there is, so the writer's failure
        path reads this to decide whether to restore it.
        """
        return self._moved_aside

    def __enter__(self) -> "ArtifactDirectoryPublication":
        """Return this publication, for use as a context manager."""
        return self

    def __exit__(self, *_exception: object) -> None:
        """Release the held descriptors.  Removes nothing - see :meth:`close`."""
        self.close()

    def close(self) -> None:
        """Release the held directory descriptors.  Idempotent.

        Deliberately removes nothing: what should happen to a staging or
        renamed-aside tree after a failure is the writer's decision, made with
        :meth:`discard_scratch` and :meth:`restore_superseded`, and a context
        manager that swept scratch on the way out would take that decision
        away - including in the one case where the renamed-aside copy is the
        only complete generation in existence.
        """
        for attribute in ("_staging_fd", "_parent_fd"):
            fd = getattr(self, attribute)
            if fd is None:
                continue
            setattr(self, attribute, None)
            try:
                os.close(fd)
            except OSError:
                # A descriptor that cannot be closed is already unusable, and
                # this module has no logger to say so with.
                pass

    # -- queries ------------------------------------------------------------

    def published_exists(self) -> bool:
        """Return whether the published tree is currently there.

        Raises:
            ArtifactPathError: If the entry exists but is a symbolic link, a
                junction or any other reparse point.  The published tree is a
                directory this writer produced; a link standing in for it is a
                redirection out of the artifact root, so it is refused here -
                before a staging tree is built - rather than renamed aside and
                replaced.
            OSError: If the entry cannot be examined.
        """
        info = self._entry_stat(self._final_name, self.final)
        if info is None:
            return False
        if _is_reparse_point(info):
            raise ArtifactPathError(
                f"the report tree entry {self._final_name!r} is a symbolic "
                "link; refusing to publish through it"
            )
        return True

    def scratch_entries(self) -> tuple[PublicationScratch, ...]:
        """Return every publication scratch directory beside the published tree.

        Returns:
            One :class:`PublicationScratch` per matching entry, ordered by
            name so a caller's choice is deterministic.  An entry that
            disappears mid-scan is skipped; an empty tuple means there is
            nothing to recover.

        Raises:
            OSError: If the parent directory cannot be listed.
        """
        prefixes = (
            f".{self.final.name}{PUBLICATION_STAGING_INFIX}",
            f".{self.final.name}{PUBLICATION_SUPERSEDED_INFIX}",
        )
        superseded_prefix = prefixes[1]
        if self._parent_fd is not None:
            names = os.listdir(self._parent_fd)
        else:
            self._verify_anchor()
            names = os.listdir(self.final.parent)
        found: list[PublicationScratch] = []
        for name in sorted(names):
            if not name.startswith(prefixes):
                continue
            path = self.final.with_name(name)
            info = self._entry_stat(name, path)
            if info is None:
                continue
            found.append(
                PublicationScratch(
                    name=name,
                    path=path,
                    is_own=name in (self.staging.name, self.superseded.name),
                    is_superseded=name.startswith(superseded_prefix),
                    is_dir=stat.S_ISDIR(info.st_mode)
                    and not _is_reparse_point(info),
                    modified_at=info.st_mtime,
                )
            )
        return tuple(found)

    def has_file(self, relative_name: str) -> bool:
        """Return whether ``relative_name`` is a regular file in the staging tree.

        The inventory check a writer makes before the swap, resolved through the
        held staging descriptor so it answers for the tree that is about to be
        published rather than for a pathname.

        Args:
            relative_name: Slash-separated path inside the staging tree.

        Returns:
            ``True`` for a regular file, ``False`` when it is absent, is a
            directory, or is a link of any kind.

        Raises:
            ArtifactPathError: If :meth:`create_staging` has not run, or the
                name is not a usable relative path.
            OSError: If the tree cannot be examined for any other reason.
        """
        parts = _relative_parts(relative_name)
        if self._staging_fd is not None:
            try:
                fd = self._descend_staging(
                    self._staging_fd, parts[:-1], create=False
                )
            except (FileNotFoundError, NotADirectoryError, ArtifactPathError):
                return False
            try:
                info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            except (FileNotFoundError, NotADirectoryError):
                return False
            finally:
                if fd != self._staging_fd:
                    os.close(fd)
        else:
            self._require_staging()
            try:
                info = os.lstat(self.staging.joinpath(*parts))
            except (FileNotFoundError, NotADirectoryError):
                return False
        return stat.S_ISREG(info.st_mode) and not _is_reparse_point(info)

    # -- building -----------------------------------------------------------

    def create_staging(self) -> Path:
        """Create the staging directory and hold it open.

        Args:
            None.

        Returns:
            The staging directory, for a log record.  Nothing else should use
            it as a path: the files inside it are created through
            :meth:`open` and :meth:`copy_in`.

        Raises:
            ArtifactPathError: If an entry is already there under the staging
                name and is a link, or is not a directory.
            OSError: If it cannot be created.
        """
        if self._parent_fd is not None:
            self._staging_fd = _descend(
                self._parent_fd, self.staging.name, create=True
            )
        else:
            self._verify_anchor()
            staging_anchor = _FallbackAnchor(self.staging)
            staging_anchor.prepare(entry_is_directory=True)
            self._staging_anchor = staging_anchor
        return self.staging

    def open(
        self,
        relative_name: str,
        *,
        binary: bool = False,
        encoding: str = "utf-8",
        newline: str = "\n",
    ) -> IO[Any]:
        """Open a file inside the staging tree for writing.

        Intermediate directories are created with :data:`ARTIFACT_DIR_MODE` and
        the file with :data:`ARTIFACT_FILE_MODE`, each component verified
        no-follow under the descriptor of its parent.  A repeated name
        overwrites in place, which is what makes a detail-page name collision
        one file on disk rather than a failure.

        Args:
            relative_name: Slash-separated path inside the staging tree, such
                as ``overview-features.html`` or ``css/cucumber.css``.
            binary: ``True`` for a binary stream.
            encoding: Text encoding; ignored when ``binary`` is set.
            newline: Newline translation; ignored when ``binary`` is set.

        Returns:
            An open file object the caller closes.

        Raises:
            ArtifactPathError: If :meth:`create_staging` has not run, the name
                is not a usable relative path, or a component of it is a link
                or is not a directory.
            OSError: If the file cannot be created or opened.
        """
        parts = _relative_parts(relative_name)
        flags = os.O_WRONLY | os.O_CREAT | _O_NOFOLLOW | _O_NONBLOCK
        destination = self._require_staging().joinpath(*parts)
        if self._staging_fd is not None:
            fd = self._descend_staging(
                self._staging_fd, parts[:-1], create=True
            )
            try:
                _refuse_unsafe_entry(fd, parts[-1])
                try:
                    handle_fd = os.open(
                        parts[-1], flags, ARTIFACT_FILE_MODE, dir_fd=fd
                    )
                except OSError as error:
                    _refuse_open_failure(error, parts[-1], action="write")
                    raise
            finally:
                if fd != self._staging_fd:
                    os.close(fd)
            return _truncated_write_stream(
                handle_fd,
                destination,
                binary=binary,
                encoding=encoding,
                newline=newline,
                verify_by_name=False,
            )

        page_anchor = _FallbackAnchor(destination)
        page_anchor.prepare()
        page_anchor.verify_entry()
        try:
            handle_fd = os.open(destination, flags, ARTIFACT_FILE_MODE)
        except OSError as error:
            _refuse_open_failure(error, destination.name, action="write")
            raise
        return _truncated_write_stream(
            handle_fd,
            destination,
            binary=binary,
            encoding=encoding,
            newline=newline,
            verify_by_name=True,
            anchor=page_anchor,
        )

    def copy_in(self, source: Path | str, relative_name: str) -> Path:
        """Copy one file into the staging tree, byte for byte.

        The bytes are read from ``source`` - a package asset, outside the
        artifact root and therefore read ordinarily - and written through
        :meth:`open`, so the copy lands under the same verified descriptor and
        the same creation mode as a rendered page.  Only the bytes are copied:
        the source's mode and timestamps are deliberately not, because an
        asset shipped ``0644`` in a wheel must not reintroduce the group and
        other bits the mode policy exists to clear.

        Args:
            source: The file to copy.
            relative_name: Slash-separated destination inside the staging tree.

        Returns:
            The destination path, for a log record or an inventory.

        Raises:
            ArtifactPathError: As :meth:`open`.
            FileNotFoundError: If ``source`` is absent.
            OSError: If it cannot be read or the destination cannot be written.
        """
        with open(source, "rb") as origin, self.open(relative_name, binary=True) as copy:
            while True:
                chunk = origin.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                copy.write(chunk)
        return self.staging.joinpath(*_relative_parts(relative_name))

    # -- publishing and scratch ---------------------------------------------

    def publish(self) -> Path:
        """Swap the staging tree into place, and return the published directory.

        Two renames rather than one replace: renaming a directory onto an
        existing directory fails on Windows and on POSIX alike, so an existing
        generation is renamed to :attr:`superseded` first - which is what
        :attr:`moved_aside` then reports - and the staging tree is renamed onto
        the published name second.  Between the two the published tree is
        *absent* rather than partial, which is the one instant this design
        cannot remove; both renames are metadata operations on one filesystem.

        Returns:
            :attr:`final`.

        Raises:
            ArtifactPathError: If :meth:`create_staging` has not run, if this
                publication has already been published, or if a link stands
                where the published tree belongs.
            OSError: If either rename fails.  A failure of the second one is
                why :attr:`moved_aside` is exposed: the previous generation is
                intact under :attr:`superseded` and the caller restores it.
        """
        if self._published:
            # Checked before the staging test, because a successful publish
            # releases the staging descriptor: a second call must be answered
            # with what actually happened rather than with "not staged".
            raise ArtifactPathError(
                "this report tree publication has already been published"
            )
        self._require_staging()
        if self.published_exists():
            self._rename_scratch(self._final_name, self.superseded.name)
            self._moved_aside = True
        self._rename_scratch(self.staging.name, self._final_name)
        self._published = True
        # The staging name now belongs to the published tree, so the descriptor
        # held for it is released: a write through it after the swap would be a
        # write into the published artifact.
        if self._staging_fd is not None:
            fd = self._staging_fd
            self._staging_fd = None
            try:
                os.close(fd)
            except OSError:
                pass
        self._staging_anchor = None
        return self.final

    def restore_superseded(self, name: str) -> None:
        """Rename a renamed-aside tree back onto the published name.

        What makes an interrupted publication recoverable: a process killed
        between the two renames of :meth:`publish` leaves no published tree and
        one renamed-aside copy that is the only complete generation in
        existence.

        Args:
            name: The scratch entry's name, from :meth:`scratch_entries`.  It
                must be a renamed-aside name for this published tree; anything
                else is refused rather than moved.

        Raises:
            ArtifactPathError: If the name is not a renamed-aside scratch name
                for this tree, or the entry is not a directory.
            OSError: If the rename fails - including when the published tree is
                already there, which is why a caller restores only into its
                absence.
        """
        self._require_scratch_name(name, superseded_only=True)
        info = self._entry_stat(name, self.final.with_name(name))
        if info is None:
            raise FileNotFoundError(
                errno.ENOENT, "no such report tree to restore", str(name)
            )
        if not stat.S_ISDIR(info.st_mode) or _is_reparse_point(info):
            raise ArtifactPathError(
                f"the report tree scratch {name!r} is not a directory; "
                "refusing to restore it"
            )
        self._rename_scratch(name, self._final_name)

    def discard_scratch(self, name: str) -> None:
        """Dispose of one publication scratch entry and everything under it.

        The only removal this module performs, and what it can promise depends
        on the platform - deliberately, because the difference is the
        difference between a bounded cleanup and one that can reach outside
        the artifact tree (CWE-59/CWE-22):

        * **With the no-follow primitives** the entry and its whole subtree are
          removed, descriptor-relative at every step, and a link of any kind
          inside it is unlinked rather than descended
          (:func:`_remove_tree_relative`).  Nothing survives the call.
        * **Without them** the entry is disposed of *without being descended*:
          removed where that needs no walk, and otherwise renamed aside to a
          dot-prefixed name no request can reach, for ``--clean`` to remove
          (:func:`_detach_tree_by_name`).  A scratch directory can therefore
          outlive the call on that branch; a cleanup that deletes something
          outside the tree cannot.

        Either way the name must be one this publication recognises, so a
        caller cannot ask it to dispose of the published tree or a sibling
        artifact.

        Args:
            name: The scratch entry's name, from :meth:`scratch_entries`, or
                :attr:`staging` / :attr:`superseded`'s own name.

        Raises:
            ArtifactPathError: If the name is not a scratch name for this
                published tree, or a bound component changed on the fallback
                branch.
            OSError: If the disposal fails for any reason other than the entry
                not being there, which is not a failure.
        """
        self._require_scratch_name(name)
        if name == self.staging.name and self._staging_fd is not None:
            fd = self._staging_fd
            self._staging_fd = None
            try:
                os.close(fd)
            except OSError:
                pass
        self._staging_anchor = None
        try:
            if self._parent_fd is not None:
                _remove_tree_relative(self._parent_fd, name)
            else:
                self._verify_anchor()
                _detach_tree_by_name(self.final.with_name(name))
        except FileNotFoundError:
            return

    # -- internals ----------------------------------------------------------

    def _entry_stat(self, name: str, path: Path) -> os.stat_result | None:
        """No-follow stat of one entry beside the published tree, or ``None``."""
        try:
            if self._parent_fd is not None:
                return os.stat(name, dir_fd=self._parent_fd, follow_symlinks=False)
            self._verify_anchor()
            return os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            return None

    def _rename_scratch(self, source: str, destination: str) -> None:
        """Rename one entry to another beside the published tree."""
        if self._parent_fd is not None:
            os.rename(
                source,
                destination,
                src_dir_fd=self._parent_fd,
                dst_dir_fd=self._parent_fd,
            )
            return
        self._verify_anchor()
        os.rename(self.final.with_name(source), self.final.with_name(destination))

    def _require_scratch_name(self, name: str, *, superseded_only: bool = False) -> None:
        """Refuse a name that is not this publication's scratch."""
        prefixes = (
            (f".{self.final.name}{PUBLICATION_SUPERSEDED_INFIX}",)
            if superseded_only
            else (
                f".{self.final.name}{PUBLICATION_STAGING_INFIX}",
                f".{self.final.name}{PUBLICATION_SUPERSEDED_INFIX}",
            )
        )
        if "/" in name or "\\" in name or not name.startswith(prefixes):
            raise ArtifactPathError(
                f"{name!r} is not a publication scratch name for this report "
                "tree; refusing to act on it"
            )

    def _require_staging(self) -> Path:
        """Return the staging directory, refusing a publication not yet staged."""
        if self._staging_fd is None and self._staging_anchor is None:
            raise ArtifactPathError(
                "the report tree staging directory has not been created; "
                "call create_staging() first"
            )
        return self.staging

    def _verify_anchor(self) -> None:
        """Re-establish the bound directory chain on the fallback branch."""
        if self._anchor is not None:
            self._anchor.verify()

    def _descend_staging(
        self, staging_fd: int, parts: tuple[str, ...], *, create: bool
    ) -> int:
        """Return a descriptor for a directory inside the staging tree.

        Args:
            staging_fd: The held staging descriptor, passed in by the caller
                that established it is there - so this helper needs no
                "what if there is none" branch that no call can reach.
            parts: Directory components beneath staging, in order.
            create: Whether to create a missing component.

        Returns:
            A descriptor for the last directory reached, which is
            ``staging_fd`` itself when ``parts`` is empty - so a caller closes
            the result only when it differs from the one it passed in.
        """
        fd = staging_fd
        for name in parts:
            next_fd = _descend(fd, name, create=create)
            if fd != staging_fd:
                os.close(fd)
            fd = next_fd
        return fd


def begin_directory_publication(
    final: Path | str, *, pid: int | None = None
) -> ArtifactDirectoryPublication:
    """Start a staged publication of the directory artifact at ``final``.

    The entry point ``app/reporting/pretty_reports.py`` uses; see
    :class:`ArtifactDirectoryPublication` for the sequence and for what it
    refuses.  The parent of ``final`` is created and verified here, so a
    symbolic link or a junction standing in for ``target/`` or for
    ``target/cucumber/`` fails before any page is rendered into a staging tree.

    Args:
        final: The published tree's directory.
        pid: Process id the scratch names carry; defaults to
            :func:`os.getpid`.

    Returns:
        A publication whose descriptors the caller releases, normally by using
        it as a context manager.

    Raises:
        ArtifactPathError: If a component from ``target`` inward is a symbolic
            link or a junction, or if ``final`` names no entry.
        OSError: If the parent cannot be created or opened.
    """
    return ArtifactDirectoryPublication(final, pid=pid)


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
        return None
    elif relative not in _ALLOWED_FILE_KEYS and not relative.startswith(
        f"{PRETTY_REPORTS_DIR_NAME}/"
    ):
        return None
    return tuple(components)


def _bound_request_anchor(path: Path) -> "_FallbackAnchor | None":
    """Bind the owned chain of a request path, on the branch that needs it.

    Called by the two request entry points **before** they resolve the name and
    check containment, so that the identities they later re-establish were
    recorded before that window rather than inside it.  A chain bound *after* a
    directory was substituted records the substitute and agrees with itself
    afterwards, which is no check at all.

    Args:
        path: The candidate artifact path, beneath the artifact root.

    Returns:
        A bound anchor on the fallback branch, or ``None`` where the primitives
        make one unnecessary.

    Raises:
        ArtifactPathError: If an owned component is a reparse point or has no
            identity to bind.
        OSError: If a component is absent or cannot be examined.  Both callers
            turn every failure into a rejected request.
    """
    if _NO_FOLLOW_SUPPORTED:
        return None
    anchor = _FallbackAnchor(path)
    anchor.bind()
    return anchor


def _verify_artifact_no_follow(
    path: Path, anchor: "_FallbackAnchor | None" = None
) -> None:
    """Refuse a resolved artifact path with a symlink from ``target`` inward.

    The validation-only counterpart of :func:`open_artifact_read`, for
    :func:`resolve_artifact`, which returns a path rather than a handle.  It
    applies the same trusted-anchor rule as the write-side helpers, so the
    validator and :func:`open_resolved_artifact` cannot reach opposite
    conclusions about the same request.

    Args:
        path: The candidate artifact path, beneath the artifact root.
        anchor: The chain :func:`_bound_request_anchor` bound before the
            caller resolved the name, re-established here.  ``None`` binds one
            now, which is all a caller that has resolved nothing needs.

    Raises:
        ArtifactPathError: If a component from ``target`` inward, or the entry
            itself, is a symbolic link, or if a bound component changed since
            the caller bound it.
        OSError: If a component cannot be examined.  Nothing is created.
    """
    if not _NO_FOLLOW_SUPPORTED:
        validation_anchor = anchor if anchor is not None else _FallbackAnchor(path)
        if anchor is None:
            validation_anchor.bind()
        validation_anchor.verify_entry()
        return
    fd, final = _verified_parent_fd(path, create=False)
    try:
        _refuse_unsafe_entry(fd, final)
    finally:
        os.close(fd)


def resolve_artifact(name: str, base: Path | str | None = None) -> Path | None:
    """Resolve an allowlisted artifact name from an HTTP request, or reject it.

    Backs ``GET /artifacts/<path:name>`` (AAP 0.3.1), which serves an
    allowlisted artifact only: the three artifact files, or a path under the
    PrettyReports directory, which itself is served its overview page.

    Rejected, in this order: an empty name; an absolute path or a UNC prefix; a
    Windows drive letter; an empty component; any component beginning with a
    dot, covering ``.`` and ``..`` traversal and the dot-prefixed per-worker
    directory alike; a directory-shaped name that is not the PrettyReports key;
    a name off the allowlist; a path escaping ``target/`` once
    :meth:`~pathlib.Path.resolve` has followed its links; a path that cannot be
    resolved at all; a path that does not exist or is a directory; and a
    symbolic link from ``target`` inward.

    Args:
        name: The raw request path segment; backslashes normalise to forward
            slashes first, so a Windows separator smuggles nothing past.
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The **resolved** path of the file to serve, so a caller cannot
        re-resolve a different object than the one validated, or ``None`` for
        every rejection - which lets ``app/errors.py`` answer one plain 404.
    """
    components = _validated_artifact_components(name)
    if components is None:
        return None

    root = target_root(base)
    candidate = root.joinpath(*components)
    try:
        # The fallback branch binds the owned chain first, so the identities
        # re-established at the end of this function predate everything below
        # (see _bound_request_anchor).
        anchor = _bound_request_anchor(candidate)
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
        return None
    try:
        # A link anywhere from target/ inward is refused outright, not merely
        # required to land inside the root: the route must serve the artifacts
        # the writers wrote, and a link in the middle of the tree is not one of
        # them.  On the fallback branch this is also where the chain bound
        # above is re-established, so a directory substituted after the
        # containment check is a rejection rather than a served file.  Any
        # failure here is a rejection, never an exception.
        _verify_artifact_no_follow(candidate, anchor)
    except (OSError, ValueError):
        return None
    return resolved


def open_resolved_artifact(
    name: str, base: Path | str | None = None
) -> tuple[Path, BinaryIO] | None:
    """Validate an untrusted artifact name and open the file, in one operation.

    The entry point for ``GET /artifacts/<path:name>`` (AAP 0.3.1): it applies
    exactly the allowlist of :func:`resolve_artifact` - the same code, so the
    two cannot drift - and then opens the file it validated, under a held
    directory descriptor and with ``O_NOFOLLOW``, before returning.  There is
    no window between the check and the open for a symlink to be swapped into
    (CWE-367), and the caller serves the handle instead of naming the path
    again.

    Args:
        name: The raw request path segment, untrusted.
        base: Directory to resolve against; defaults to the working directory.

    Returns:
        The resolved path and an open binary stream over it - the caller owns
        the stream and closes it, normally by handing it to the response - or
        ``None`` for every rejection, including an absent file, a directory, a
        symbolic link, a path escaping the artifact root and a name off the
        allowlist.  :exc:`ArtifactPathError`, every other :exc:`OSError` and
        :exc:`ValueError` are absorbed into that ``None``, and neither the
        attempted name nor any filesystem path is logged or put in a message,
        so ``app/errors.py`` answers one plain 404.  An interrupt
        (:exc:`KeyboardInterrupt`, :exc:`SystemExit`) still propagates.
    """
    components = _validated_artifact_components(name)
    if components is None:
        return None

    root = target_root(base)
    candidate = root.joinpath(*components)
    try:
        # Bound before the name is resolved, for the reason
        # _bound_request_anchor states.
        anchor = _bound_request_anchor(candidate)
        resolved_root = root.resolve()
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return None
    if not resolved.is_relative_to(resolved_root):
        # Made before anything is opened, so a lexically escaping name never
        # reaches an open call on a platform without O_NOFOLLOW.
        return None

    handle: BinaryIO | None = None
    try:
        handle = open_artifact_read(candidate)
        if anchor is not None:
            # The fallback branch's last check: the chain bound before the
            # containment test above is re-established now that the file is
            # open, so a directory substituted anywhere in that window is a
            # rejected request rather than a served file.  The open itself
            # already refused a link and compared the object it holds with what
            # the name reports.
            anchor.verify()
    except (OSError, ValueError):
        # ArtifactPathError for a symlink, a non-regular file or a component
        # that changed, FileNotFoundError for an absent one, PermissionError
        # for an unreadable one: all one 404.
        if handle is not None:
            handle.close()
        return None
    # The open is the decisive step.  Every component from target/ inward was
    # verified not to be a symlink in the operation that produced this handle,
    # and none of the validated components is empty, dot-prefixed or a
    # separator, so the object now held open is the one at ``resolved`` - and no
    # later change to the pathname can alter what the caller serves.
    return resolved, handle


def normalize_feature_uri(value: str) -> str:
    """Rewrite a legacy feature-directory prefix to the one this port emits.

    AAP deviation 1 moves the features from ``src/main/resources/features/`` to
    ``features/`` while preserving their filenames.  The golden fixtures are
    stored verbatim, with the legacy prefix, so the writer tests compare
    against them through this single helper (AAP 0.4.1).

    The rewrite is a prefix substitution on the string, so anything following
    the path - notably the ``:9:24`` line-number suffixes of the rerun manifest
    (AAP 0.6) - is preserved by construction, and a leading ``file:`` scheme is
    carried through untouched.

    Args:
        value: A feature URI or rerun-manifest entry, with or without the
            ``file:`` scheme and with or without trailing line numbers.

    Returns:
        The value with a leading legacy feature prefix replaced by
        ``features/``.  Anything else comes back unchanged, which makes the
        function idempotent; the segment is matched only at the start.

    Examples:
        >>> normalize_feature_uri("file:src/main/resources/features/Crm.feature")
        'file:features/Crm.feature'
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


# Package-relative locators, derived from ``__file__`` rather than the working
# directory: they have to stay correct in a worker process running from any
# directory and outside any Flask application context, where ``static_folder``
# is unavailable.


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
