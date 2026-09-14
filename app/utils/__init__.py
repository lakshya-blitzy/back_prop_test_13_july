"""Foundation layer of the Testinium-QA Python port: paths and properties.

The two lowest-level modules of the port, and nothing else:

``paths``
    The single source of truth for every filesystem path the port touches -
    the four report artifacts, the per-worker intermediate directory beneath
    them, the feature directory, and the package-relative template, static and
    vendored-asset locators.  The technical specification (section 0.4.2)
    assigns that ownership explicitly: every writer, the HTTP artifact route,
    the ``--clean`` step and the per-worker engine invocation take their paths
    from this module, and no other Python module in the port spells out a path
    literal.  Because the build output directory is generated content that
    outlives a run, the same module is also the **write authority** for it: the
    report writers and the collector's own output stream take a stream from
    ``open_artifact_write``, the single-page HTML artifact is published through
    ``publish_artifact_file``, and the PrettyReports tree through
    ``begin_directory_publication`` - and no writer validates a pathname and
    then re-opens it.  What those entry points can promise depends on the
    platform's primitives, and the difference is stated rather than glossed:
    where ``O_NOFOLLOW`` and ``dir_fd`` exist they create, verify, write and
    rename under a held directory descriptor, so a symbolic link or a junction
    left in the build output cannot redirect a write outside the artifact root
    at all; where they do not - Windows - a pathname is the only handle
    available, so the same entry points **detect and refuse** instead: a
    reparse point is rejected outright, every owned directory's identity is
    bound before the request and re-established before each destructive step
    and after each open, an identity that cannot be obtained fails the call
    closed, and no directory is ever descended by name.  A won race on that
    branch is a refused run, not a redirected write; ``paths`` documents the
    residual difference where it is implemented.
    The read side is ``open_artifact_read``, ``read_artifact_text`` and
    ``open_resolved_artifact``, the last of which validates an untrusted
    request name and opens what it validated in one operation;
    ``resolve_artifact`` is its validation-only counterpart, for a caller that
    needs the path rather than the handle.  ``ensure_dir`` and
    ``ensure_parent`` remain available for a caller that needs a directory
    rather than a stream - they are not a substitute for the write entry
    points, because a caller that reopens the path they return has reopened it.
    Generated directories are created ``0700`` and generated files ``0600``,
    tightened through their own descriptors where an earlier run left them more
    permissive; that policy is POSIX-only, since a Windows mode carries no such
    meaning.
``properties``
    The ``java.util.Properties``-compatible reader replacing the Java
    ``ConfigurationReader``, with its one-time load, its tolerance of a
    missing ``configuration.properties`` and its ``None`` for an absent key.
    ``app/config.py`` is the only module permitted to import it, which is what
    keeps that file read in one place.

Import boundary (AAP 0.4.2)
---------------------------
This package imports nothing from the wider ``app`` package: both siblings
import only the standard library, and this file imports only those two
siblings.  That is load-bearing rather than stylistic - the suite shards
across worker processes that must resolve their own output paths, and because
importing ``app.utils.paths`` first executes ``app/__init__.py``, one stray
import here would put Flask into every worker.

This file marks the package and re-exports its siblings' public names.  It
holds no logic and has no import-time side effects: importing ``app.utils``
configures no logging, creates no directory and reads no file, and in
particular does not touch ``configuration.properties`` - that load is lazy, on
the first :func:`get_properties` call.  ``__all__`` groups the re-exports by
owning module instead of sorting across both, which is what the lint
suppression on it records.

``properties.reset_cache`` (test support for returning to the pre-load state)
and ``properties.logger`` (that module's private channel) are deliberately not
re-exported; both stay reachable through their defining module.
"""

from .paths import (
    ARTIFACT_DIR_MODE,
    ARTIFACT_FILE_MODE,
    ARTIFACT_MODE_MASK,
    ARTIFACT_SPECS,
    CUCUMBER_JSON_NAME,
    CUCUMBER_JSON_RELPATH,
    CUCUMBER_REPORTS_HTML_NAME,
    CUCUMBER_REPORTS_HTML_RELPATH,
    FEATURES_DIR_NAME,
    FILE_URI_SCHEME,
    LEGACY_FEATURES_PREFIX,
    NORMALIZED_FEATURES_PREFIX,
    PRETTY_HTML_SUBDIR,
    PRETTY_OVERVIEW_INDEX,
    PRETTY_REPORTS_DIR_NAME,
    PRETTY_REPORTS_RELPATH,
    PUBLICATION_STAGING_INFIX,
    PUBLICATION_SUPERSEDED_INFIX,
    RERUN_TXT_NAME,
    RERUN_TXT_RELPATH,
    TARGET_DIR_NAME,
    WORKERS_DIR_NAME,
    ArtifactDirectoryPublication,
    ArtifactPathError,
    ArtifactSpec,
    PublicationScratch,
    artifact_path,
    begin_directory_publication,
    cucumber_json_path,
    cucumber_reports_html_path,
    ensure_dir,
    ensure_parent,
    features_dir,
    iter_worker_result_paths,
    normalize_feature_uri,
    open_artifact_read,
    open_artifact_write,
    open_resolved_artifact,
    package_root,
    pretty_reports_dir,
    pretty_reports_html_dir,
    pretty_reports_index_path,
    publish_artifact_file,
    read_artifact_text,
    rerun_txt_path,
    resolve_artifact,
    static_dir,
    target_root,
    templates_dir,
    vendor_dir,
    worker_result_path,
    workers_dir,
)
from .properties import (
    DEFAULT_ENCODING,
    MISSING_FILE_MESSAGE,
    PROPERTIES_FILENAME,
    get_properties,
    get_property,
    load_properties,
    parse_properties,
)

__all__ = [  # noqa: RUF022
    "TARGET_DIR_NAME",
    "FEATURES_DIR_NAME",
    "WORKERS_DIR_NAME",
    "PRETTY_REPORTS_DIR_NAME",
    "PRETTY_HTML_SUBDIR",
    "PRETTY_OVERVIEW_INDEX",
    "CUCUMBER_REPORTS_HTML_NAME",
    "CUCUMBER_JSON_NAME",
    "RERUN_TXT_NAME",
    "CUCUMBER_REPORTS_HTML_RELPATH",
    "CUCUMBER_JSON_RELPATH",
    "RERUN_TXT_RELPATH",
    "PRETTY_REPORTS_RELPATH",
    "FILE_URI_SCHEME",
    "LEGACY_FEATURES_PREFIX",
    "NORMALIZED_FEATURES_PREFIX",
    "ArtifactSpec",
    "ARTIFACT_SPECS",
    "target_root",
    "features_dir",
    "cucumber_reports_html_path",
    "cucumber_json_path",
    "rerun_txt_path",
    "pretty_reports_dir",
    "pretty_reports_html_dir",
    "pretty_reports_index_path",
    "artifact_path",
    "workers_dir",
    "worker_result_path",
    "iter_worker_result_paths",
    "ensure_dir",
    "ensure_parent",
    "ArtifactPathError",
    "open_artifact_write",
    "publish_artifact_file",
    "open_artifact_read",
    "read_artifact_text",
    # Creation-mode policy for generated directories and files.
    "ARTIFACT_DIR_MODE",
    "ARTIFACT_FILE_MODE",
    "ARTIFACT_MODE_MASK",
    # Staged publication of the directory artifact.
    "begin_directory_publication",
    "ArtifactDirectoryPublication",
    "PublicationScratch",
    "PUBLICATION_STAGING_INFIX",
    "PUBLICATION_SUPERSEDED_INFIX",
    # Artifact lookup and feature-URI normalization.
    "resolve_artifact",
    "open_resolved_artifact",
    "normalize_feature_uri",
    "package_root",
    "templates_dir",
    "static_dir",
    "vendor_dir",
    "parse_properties",
    "load_properties",
    "get_properties",
    "get_property",
    "PROPERTIES_FILENAME",
    "MISSING_FILE_MESSAGE",
    "DEFAULT_ENCODING",
]
