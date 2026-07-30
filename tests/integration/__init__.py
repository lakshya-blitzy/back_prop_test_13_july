"""Package marker for the ``tests.integration`` package.

WHY THIS FILE EXISTS
--------------------
This module has exactly one job: to make ``tests/integration`` an importable
Python package, so that every ``test_*.py`` module beside it resolves as a real
dotted module path (``tests.integration.test_api_routes``) rather than as a
loose top-level module. That is a packaging necessity, not a behavioural
choice, and it matters concretely here for two reasons.

First, the ported suite runs in parallel: ``pytest-xdist`` is invoked with
``-n logical``, the translation of Maven Surefire's
``<parallel>methods</parallel>`` together with
``<useUnlimitedThreads>true</useUnlimitedThreads>`` declared at
``[pom.xml:L22-L23]``. Every xdist worker is a separate operating-system
process, and a package-qualified module path removes any dependence on
``rootdir`` discovery or on ``sys.path`` ordering inside those workers.

Second, the sibling suites ``tests/unit`` and ``tests/parity`` are packages
too, so package qualification is also what stops identically named modules in
the three suites colliding during collection.

WHAT THE SIBLING MODULES ASSERT
-------------------------------
The seven test modules in this package carry the integration-level validation
criteria of the migration:

``test_api_routes.py``
    Exercises every route of the closed ten-route HTTP surface (criterion V11).
    That surface is the mechanical projection of the three pipeline stages at
    ``[Jenkins:L2-L16]`` onto HTTP endpoints, so every route traces back to a
    named stage or to a report artifact instead of inventing capability.

``test_report_endpoints.py``
    Asserts that each generated report artifact is retrievable over HTTP
    (criterion V11). The artifact set is the four Cucumber plugin declarations
    at ``[README.md:L79-L82]``: the HTML report, the JSON report, the rerun
    manifest and the PrettyReports directory.

``test_pipeline_service.py``
    Asserts stage ordering and, critically, that report generation still runs
    after a failed test stage (criterion V12). That is the non-gating behaviour
    of the source pipeline at ``[Jenkins:L1-L17]``, whose publisher thresholds
    are all ``-1`` and whose Surefire configuration ignores test failures.

``test_run_flow.py``
    Asserts that a run which starts with ``target/`` absent still writes
    ``target/cucumber.json`` (criterion V7). The Cucumber JSON writer does not
    create its own parent directory, so this closes a silent-failure trap that
    Maven used to hide by creating ``target/`` implicitly.

``test_parallel_execution.py``
    Asserts that a parallel run still produces a complete JSON report, and that
    the incompatible Gherkin terminal reporter stays out of the default option
    set (criterion V8) because it aborts the session when combined with xdist.

``test_rerun_manifest.py``
    Asserts that ``target/rerun.txt`` holds exactly one deterministic
    ``<uri>:<line>`` entry per failing scenario, derived out of the JSON report
    so that the manifest can never drift away from it (criterion V9).

``test_web_routes.py``
    Asserts the rendered report index and the rendered error pages served by
    the web blueprint (criterion V11).

SOURCE LINEAGE
--------------
This file has no source construct. It appears in the migration plan purely
because Python requires a marker module for a package; nothing in the source
project corresponds to it. ``README.md`` is recorded as its source file only
because the plan designates that document the primary behavioural
specification out of which the whole ``tests/`` tree is materialised. No line
of ``README.md`` is ported into this module, and none should be.

DEPENDENCY DIRECTION
--------------------
Imports flow one way only: ``tests/`` may depend on ``app/``, while nothing
under ``app/`` may ever depend on ``tests/``. That rule keeps the deployable
application independent of the test harness even though both are derived out
of the same source specification, and this marker must never host a re-export
shim that would invite the reverse edge.

DELIBERATELY INERT
------------------
The body of this module is this docstring and nothing else. There is no import
statement of any kind, not even a standard-library one; no ``__all__``; no
``__version__``; no re-export shim; no test-scaffolding helper, hook, mark
registration or plugin declaration; and no side effect whatsoever, meaning no
filesystem access, no directory creation, no environment read and no
``sys.path`` manipulation. Importing ``tests.integration`` is therefore free
and total.

The reason is measured rather than stylistic. Importing any ``app.*`` module
executes ``app/__init__.py`` first, and that module is the Flask application
factory, so every ``app.*`` import transitively requires Flask to be
installed. Were this marker to import anything, one missing distribution would
break the whole package import and take unrelated suites down with it,
including the parity suite, which needs no third-party package at all and is
the acceptance gate of the migration. Keeping the marker inert confines a
missing optional dependency to the single module that genuinely needs it,
where it degrades into a skip instead of a collection error.

Responsibilities that deliberately live elsewhere: the six Gherkin tags are
registered as pytest marks exclusively in ``pytest.ini``, and creation of the
``target/`` tree belongs exclusively to ``app/utils/paths.py``, the
``Makefile`` test target and ``tests/conftest.py`` -- this package adds no
fourth owner of either. It also holds no browser automation, no stand-in for
the external application under test and no design-system concern, because
integration parity here is structural: routes, artifacts, schemas, constants
and exit codes.
"""
