"""Foundation layer of the Testinium-QA Python port: paths and properties.

This package holds the two lowest-level modules of the port, and nothing else:

``paths``
    The single source of truth for every filesystem path the port touches -
    the four report artifacts, the per-worker intermediate directory beneath
    them, the feature directory, and the package-relative template, static and
    vendored-asset locators.  The technical specification (section 0.4.2)
    assigns that ownership explicitly: every writer, the HTTP artifact route,
    the ``--clean`` step and the per-worker engine invocation take their paths
    from this module, and no other Python module in the port spells out a path
    literal.
``properties``
    The ``java.util.Properties``-compatible reader that replaces the Java
    ``ConfigurationReader``, reproducing its one-time load, its tolerance of a
    missing ``configuration.properties`` and its ``None``-for-an-absent-key
    behaviour.  It is the only reader of that file anywhere in the port, and
    ``app/config.py`` is the only module permitted to import it.

Import boundary
---------------
**This package imports nothing from the wider ``app`` package.**  Both
sibling modules import only the standard library, and this file imports only
those two siblings, so the whole package sits at the very bottom of the import
graph described in specification section 0.4.2.  Nothing here reaches
``app.config``, ``app.services``, ``app.reporting``, ``app.pages``,
``app.automation``, ``app.web`` or ``app.logging_config``, and nothing here
imports Flask, behave, Selenium, Click or Jinja2.

That is a load-bearing guarantee rather than a stylistic preference.
``app/services/test_run_service.py`` shards scenarios across a pool of worker
processes - the port's stand-in for the source build's ``parallel=methods``
with ``useUnlimitedThreads`` (``pom.xml:22-23``) - and each worker has to
resolve its own output path without a web framework being dragged into the
process to do it.  Because importing ``app.utils.paths`` first executes the
application package's ``__init__`` and then this file, a single stray import
here would put Flask into every worker.  Hence: two relative imports, and
nothing heavier.

Contract of this file
---------------------
It marks the package and re-exports its siblings' public names.  It contains
**no logic** - no functions, no classes, no branches, no computed values, no
error handling around the imports - and **no import-time side effects**:
importing ``app.utils`` configures no logging, creates no directory, reads no
file and does not inspect the working directory.  In particular it does not
touch ``configuration.properties``; that load is lazy and happens only on the
first :func:`get_properties` call, so the missing-file warning belongs to that
call and never to this import.  No project version is declared here either -
``pyproject.toml`` owns it, mapping the source build's ``1.0-SNAPSHOT``
(``pom.xml:9``) to ``1.0.0.dev0``.

Two names are re-exported by neither group, deliberately, so that the
package's advertised surface stays honest:

* ``properties.reset_cache`` is test support.  It exists only so
  ``tests/test_properties.py`` can return to the pre-load state and prove the
  one-time load, the single missing-file log record and the never-re-read
  guarantee; that test imports it from ``app.utils.properties`` directly.
  Production code calling it would reintroduce exactly the mid-run re-read
  that parity with the JVM rules out.
* ``properties.logger`` is that module's private logging channel, not an API.

Both are reachable through their defining module for anyone who genuinely
needs them; neither is part of what this package advertises.
"""

# Every name below is re-exported, and every one appears in ``__all__``.  The
# imports are named individually rather than star-imported so that the surface
# is greppable and a typo fails loudly, at import time, instead of silently
# thinning the package's API.  Member order within each import follows the
# defining module's own ``__all__``.
from .paths import (
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
    RERUN_TXT_NAME,
    RERUN_TXT_RELPATH,
    TARGET_DIR_NAME,
    WORKERS_DIR_NAME,
    ArtifactSpec,
    artifact_path,
    cucumber_json_path,
    cucumber_reports_html_path,
    ensure_dir,
    ensure_parent,
    features_dir,
    iter_worker_result_paths,
    normalize_feature_uri,
    package_root,
    pretty_reports_dir,
    pretty_reports_html_dir,
    pretty_reports_index_path,
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

# Grouped by owning module - every ``paths`` name first, then every
# ``properties`` name - and by theme within each group, because that is what
# makes a barrel readable as documentation of the package's surface.  A single
# sequence sorted across the whole list would interleave the two modules and
# lose that, so the ordering rule is suppressed for this assignment alone.
__all__ = [  # noqa: RUF022
    # -- app/utils/paths.py -------------------------------------------------
    # Directory name components.
    "TARGET_DIR_NAME",
    "FEATURES_DIR_NAME",
    "WORKERS_DIR_NAME",
    # PrettyReports tree name components.
    "PRETTY_REPORTS_DIR_NAME",
    "PRETTY_HTML_SUBDIR",
    "PRETTY_OVERVIEW_INDEX",
    # Artifact file names, which double as the HTTP allowlist keys.
    "CUCUMBER_REPORTS_HTML_NAME",
    "CUCUMBER_JSON_NAME",
    "RERUN_TXT_NAME",
    # The four artifact paths as POSIX-style relative strings.
    "CUCUMBER_REPORTS_HTML_RELPATH",
    "CUCUMBER_JSON_RELPATH",
    "RERUN_TXT_RELPATH",
    "PRETTY_REPORTS_RELPATH",
    # Feature-URI scheme and directory prefixes.
    "FILE_URI_SCHEME",
    "LEGACY_FEATURES_PREFIX",
    "NORMALIZED_FEATURES_PREFIX",
    # The artifact model.
    "ArtifactSpec",
    "ARTIFACT_SPECS",
    # Working-directory-relative accessors.
    "target_root",
    "features_dir",
    "cucumber_reports_html_path",
    "cucumber_json_path",
    "rerun_txt_path",
    "pretty_reports_dir",
    "pretty_reports_html_dir",
    "pretty_reports_index_path",
    "artifact_path",
    # Per-worker intermediate results.
    "workers_dir",
    "worker_result_path",
    "iter_worker_result_paths",
    # Directory creation helpers.
    "ensure_dir",
    "ensure_parent",
    # Artifact lookup and feature-URI normalization.
    "resolve_artifact",
    "normalize_feature_uri",
    # Package-relative locators, independent of the working directory.
    "package_root",
    "templates_dir",
    "static_dir",
    "vendor_dir",
    # -- app/utils/properties.py --------------------------------------------
    # The reader itself, from the lowest layer upwards.
    "parse_properties",
    "load_properties",
    "get_properties",
    "get_property",
    # Format and parity constants.
    "PROPERTIES_FILENAME",
    "MISSING_FILE_MESSAGE",
    "DEFAULT_ENCODING",
]
