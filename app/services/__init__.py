"""Service layer of the Testinium-QA Python port - and its public barrel.

This package holds the two services that make a run happen and turn what it did
into artifacts, and nothing else:

``test_run_service``
    Scenario selection, sharding, per-worker engine invocation and the merge -
    the executable form of the Java build's test-execution configuration
    (``pom.xml:21-29``: ``parallel=methods``, ``useUnlimitedThreads=true``,
    ``testFailureIgnore=true``).  None of that configuration ever executed in
    the source project, whose own pipeline command reports "No tests to run.",
    so this module is where the decision to **activate** that latent behaviour
    lands: it is the thing that makes the suite run.  It exits no process and
    never turns a scenario outcome into an error - everything it learns is
    reported through :class:`RunOutcome`.
``report_service``
    The fan-out that drives one merged result set through the four writers, in
    :data:`WRITER_SEQUENCE` order - the port of the plugin list the Java runner
    declared (``CukesRunner.java:9-14``).  One merged result set, four
    independent writers, none aware of the others.

The two services never import each other.  ``app/cli.py`` connects them: it
calls :func:`run_suite`, reads the outcome, then hands the merged document to
:func:`generate_reports` - and it owns the ``--clean`` step and the exit codes,
neither of which lives here.

Contract of this file
---------------------
It marks the package and re-exports its two siblings' public names, so that
``app/cli.py``, ``tests/test_test_run_service.py`` and
``tests/test_report_service.py`` have one import surface for the package.  It
contains **no logic** - no functions, no classes, no branches, no computed
values, no error handling around the imports - and **no import-time side
effects**: importing ``app.services`` configures no logging, creates no
directory (neither the artifact root nor the per-worker directory in it), reads
no file, inspects no environment variable and does not look at the working
directory.  Every one of those happens later, inside a call.  No project
version is declared here either; ``pyproject.toml`` owns it, mapping the source
build's ``1.0-SNAPSHOT`` (``pom.xml:9``) to ``1.0.0.dev0``.

Import boundary
---------------
**This file imports its two siblings and nothing else.**  It reaches no
``flask``, no ``selenium``, no ``app.config``, no ``app.utils``, no
``app.reporting`` and no ``app.logging_config``: whatever the services need,
they import for themselves, which is what keeps the dependency edge of
specification section 0.4.2 one-way - ``app/cli.py`` -> ``app.services`` ->
(``app.reporting``, ``app.utils``), with nothing in ``app/reporting/``,
``app/pages/`` or ``app/automation/`` ever importing a service.  No path
literal appears here either: ``app/utils/paths.py`` owns every path in the
port, and both services take theirs from it.

Adding nothing of its own is also what keeps this import as cheap as the
package can be.  Importing it first executes the application package's
``__init__``, which defers its Flask, blueprint and CLI imports into
``create_app()`` precisely so that ``app.utils`` stays importable in a worker
process that never builds a web application; a stray import here would undo
that.  What the two siblings do pull - behave's Gherkin parser, the
tag-expression parser, the reporting barrel - is what each needs to do its job,
and a worker that only has to resolve its own output path imports
``app.utils.paths`` directly rather than coming through this package.

Two kinds of name are re-exported by neither group, deliberately, so that the
package's advertised surface stays honest:

* ``test_run_service.WorkerProcess`` and ``test_run_service.SpawnCallable``
  describe the injectable ``spawn`` seam behind :func:`run_suite` - the
  test-only hook that stands in for the real subprocess launch, whose default
  is that module's own private launcher.  ``tests/test_test_run_service.py``
  imports them from ``app.services.test_run_service`` directly, exactly as
  ``app/utils/__init__.py`` withholds its siblings' cache-reset helper.
* Each service's ``logger`` is that module's private logging channel, not an
  API.  The handler split those channels depend on - progress to stdout,
  diagnostics to stderr - is installed by ``app/logging_config.py`` from the
  process entry points, and never from here.

Both are reachable through their defining module for anyone who genuinely needs
them; neither is part of what this package advertises.
"""

# Every name below is re-exported, and every one appears in ``__all__``.  The
# imports are named individually rather than star-imported so that the surface
# is greppable and a typo fails loudly, at import time, instead of silently
# thinning the package's API.  Both statements list exactly the ``__all__`` of
# the module they draw from - nothing is added to a service's public surface
# here, and nothing is quietly dropped from it - and member order within each
# statement follows that same declaration.
from .report_service import (
    WRITER_SEQUENCE,
    ReportOutcome,
    WriterResult,
    WriterSpec,
    generate_reports,
)
from .test_run_service import (
    NEUTRAL_TAG_EXPRESSION,
    RunOutcome,
    ScenarioRef,
    ShardPlan,
    ShardResult,
    build_worker_command,
    cleanup_workers_dir,
    default_worker_count,
    merge_worker_results,
    prepare_workers_dir,
    run_suite,
    select_rerun_scenarios,
    select_scenarios,
    shard_scenarios,
)

# Grouped by owning module - every ``test_run_service`` name first, then every
# ``report_service`` name - and ordered within each group as a run proceeds:
# count the workers, select, shard, build the command, prepare and clean the
# intermediate directory, merge, run, then hand the merged document to the
# writers.  That is what makes a barrel readable as documentation of the
# package's surface, and it is the run-then-report direction the section 0.4.2
# graph draws; the import statements above are alphabetical instead because
# that is the order import tooling keeps them in.  A single sequence sorted
# across the whole list would interleave the two modules and lose the grouping,
# so the ordering rule is suppressed for this assignment alone.
__all__ = [  # noqa: RUF022
    # -- app/services/test_run_service.py: making the suite run -------------
    # Concurrency, reproducing surefire's parallel configuration.
    "default_worker_count",
    # Selection: from the feature files, or from the rerun manifest.
    "select_scenarios",
    "select_rerun_scenarios",
    "shard_scenarios",
    # One worker invocation, and the intermediate directory it writes into.
    "build_worker_command",
    "prepare_workers_dir",
    "cleanup_workers_dir",
    # Putting the shards back together, and the whole run in one call.
    "merge_worker_results",
    "run_suite",
    # The run model: what a shard is, and what a finished run reports.
    "ScenarioRef",
    "ShardPlan",
    "ShardResult",
    "RunOutcome",
    # The tag expression that clears ``behave.ini``'s ``default_tags``.
    "NEUTRAL_TAG_EXPRESSION",
    # -- app/services/report_service.py: producing the four artifacts -------
    # The fan-out itself, and the order it drives.
    "generate_reports",
    "WRITER_SEQUENCE",
    "WriterSpec",
    # What the fan-out reports back.
    "WriterResult",
    "ReportOutcome",
]
