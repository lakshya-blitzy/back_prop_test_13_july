"""Service layer of the Testinium-QA Python port - and its public barrel.

The package holds the two services that make a run happen and turn what it did
into artifacts, and nothing else:

``test_run_service``
    Scenario selection, sharding, per-worker engine invocation and the merge -
    the executable form of the Java build's test-execution configuration
    (``pom.xml:21-29``: ``parallel=methods``, ``useUnlimitedThreads=true``,
    ``testFailureIgnore=true``), which never ran in the source project.  It
    exits no process and turns no scenario outcome into an error: everything it
    learns is reported through :class:`RunOutcome`.
``report_service``
    The fan-out that drives one merged result set through the four writers in
    :data:`WRITER_SEQUENCE` order - the port of the plugin list the Java runner
    declared (``CukesRunner.java:9-14``) - in AAP 0.3.3's shape: one merged
    result set, four independent writers, none aware of the others.

The two services never import each other.  ``app/cli.py`` connects them: it
calls :func:`run_suite`, reads the outcome, hands the merged document to
:func:`generate_reports`, and owns the ``--clean`` step and the exit codes.

This file marks the package and re-exports both siblings' public names, so the
CLI and the unit suite have one import surface for it.  It holds no logic and
has no import-time side effect: importing ``app.services`` configures no
logging, creates no directory, reads no file and inspects no environment
variable.  It imports its two siblings and nothing else, keeping the AAP 0.4.2
dependency edge one-way - ``app/cli.py`` -> ``app.services`` ->
(``app.reporting``, ``app.utils``) - and it spells no path, since
``app/utils/paths.py`` owns every path in the port.

Two kinds of name are deliberately not re-exported, so the advertised surface
stays honest: ``WorkerProcess`` and ``SpawnCallable``, the test-only ``spawn``
seam behind :func:`run_suite`, and each service's ``logger``, a private channel
rather than an API whose handler split ``app/logging_config.py`` installs from
the process entry points.  Both stay reachable through their defining module.

``__all__`` is grouped by owning module and ordered as a run proceeds rather
than sorted across the two groups, which its ``noqa`` suppresses.
"""

from .report_service import (
    WRITER_SEQUENCE,
    PublicationBoundaryLost,
    PublicationGuard,
    ReportOutcome,
    WriterResult,
    WriterSpec,
    generate_reports,
)
from .test_run_service import (
    NEUTRAL_TAG_EXPRESSION,
    RUN_LOCK_NAME,
    RunLock,
    RunOutcome,
    ScenarioRef,
    ShardPlan,
    ShardResult,
    acquire_run_lock,
    build_worker_command,
    cleanup_workers_dir,
    default_worker_count,
    delete_verified_entry,
    merge_worker_results,
    prepare_workers_dir,
    reclaim_workers_root,
    run_directory_is_active,
    run_directory_owner,
    run_suite,
    select_rerun_scenarios,
    select_scenarios,
    shard_scenarios,
    terminate_live_workers,
)

__all__ = [  # noqa: RUF022
    "default_worker_count",
    "select_scenarios",
    "select_rerun_scenarios",
    "shard_scenarios",
    "build_worker_command",
    "prepare_workers_dir",
    "cleanup_workers_dir",
    # The one destructive primitive, shared with ``app/cli.py``'s clean step:
    # a link and a reparse point are refused, and a directory is made
    # unaddressable before it is removed recursively.
    "delete_verified_entry",
    # Reclaiming that directory across invocations: what an abandoned run
    # left behind goes, what a run still in progress holds stays.
    "reclaim_workers_root",
    "run_directory_owner",
    "run_directory_is_active",
    # The claim one run holds on a checkout's build output, from before the
    # clean until after the publication, so two runs sharing a checkout cannot
    # empty each other's output or publish a mixture of both runs' reports.
    "acquire_run_lock",
    "RunLock",
    "RUN_LOCK_NAME",
    # Stopping the workers a run started, for an interrupted run.
    "terminate_live_workers",
    "merge_worker_results",
    "run_suite",
    "ScenarioRef",
    "ShardPlan",
    "ShardResult",
    "RunOutcome",
    "NEUTRAL_TAG_EXPRESSION",
    "generate_reports",
    "WRITER_SEQUENCE",
    "WriterSpec",
    "WriterResult",
    "ReportOutcome",
    # The claim the fan-out publishes under, and what it reports when that
    # claim is gone.
    "PublicationGuard",
    "PublicationBoundaryLost",
]
