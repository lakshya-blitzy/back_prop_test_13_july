"""Report-generation layer of the Testinium-QA Python port - and its public barrel.

The event collector, the four writers that turn a merged record into the source
build's four artifacts, the normalized model the two HTML writers read, and the
screenshot helpers - the runner's plugin list (``CukesRunner.java:9-14``) in
the shape section 0.3.3 states: one merged result set, four independent
writers.

``events``
    The result document and the behave formatter that fills it: the schema
    owner the writers and the model read (section 0.6).
``screenshots``
    Capture, encoding and the embedding mapping (``Hooks.java:15``): failure
    only, once, before the driver quits; failures suppressed (deviation 19).
``aggregation``
    The one normalized result model - the single place a status is normalized
    and folded, a step counted, a duration summed and a run's start chosen.
    ``html_report`` and ``pretty_reports`` import it directly; it is
    deliberately not re-exported here, so this barrel stays the writers'
    fan-out surface.
``cucumber_json``
    The machine-read JSON report, in the Cucumber-JVM schema (``Jenkins:15``).
``rerun_report``
    The rerun manifest and its parser (``FailedTestRunner.java:11``).
``html_report``
    The self-contained single page (``io.cucumber:html-formatter`` 17.0.0).
``pretty_reports``
    The PrettyReports tree and its vendored offline assets
    (``net.masterthought:cucumber-reporting`` 5.6.1).

Twenty-four names are exported, grouped in ``__all__`` by owning module, not
all consumed alike.  ``app/services/report_service.py`` imports
``write_cucumber_json``, ``write_rerun_txt``, ``write_html_report`` and
``write_pretty_reports`` *through this barrel*; the unit suite imports those
four and ``new_result_set`` from here too.  Nine have a production consumer
that names the defining module instead: ``FORMATTER_SCOPED_NAME``,
``ResultSetError``, ``new_result_set``, ``load_result_set``,
``merge_result_sets`` and ``parse_rerun_file`` reach ``test_run_service``,
``capture_png`` and ``DEFAULT_MIME_TYPE`` reach ``features/environment.py``,
and ``ResultCollectorFormatter`` has no import site anywhere, behave loading it
from ``FORMATTER_SCOPED_NAME``.  The remaining eleven - ``SCHEMA_VERSION``,
``dump_result_set``, ``iter_scenarios``, ``capture_failure_embedding``,
``build_embedding``, ``encode_png``, ``build_cucumber_json``,
``build_rerun_lines``, ``render_html_report``, ``render_pretty_pages`` and
``copy_pretty_assets`` - are consumed today by the unit suite or offered as a
convenience: the pure halves let the artifact contract be tested in memory
(section 0.5.1), and the two embedding helpers compose an attachment in one
call the scenario hook does not make.  Each ``write_*`` takes the merged
document first and accepts ``base``.  **Pass it by keyword**: ``base`` is the
second positional parameter of three writers and the third of
``write_rerun_txt``, whose second is ``path``, so a positional call misplaces
the manifest.

**Nothing in this package imports a service** - section 0.4.2 draws the edge as
``SV --> RP`` only - and nothing here imports Flask or Selenium.  This file
adds no logic and no import-time side effect.  The six import statements below
are foundational-first - ``events``, ``screenshots``, then the four writers - a
correctness requirement: importing any submodule runs this file first.  Do not
re-sort them.
"""

from .events import (  # noqa: I001
    SCHEMA_VERSION,
    FORMATTER_SCOPED_NAME,
    ResultCollectorFormatter,
    ResultSetError,
    new_result_set,
    load_result_set,
    merge_result_sets,
    dump_result_set,
    iter_scenarios,
)
from .screenshots import (
    DEFAULT_MIME_TYPE,
    capture_failure_embedding,
    build_embedding,
    capture_png,
    encode_png,
)
from .cucumber_json import build_cucumber_json, write_cucumber_json
from .rerun_report import build_rerun_lines, write_rerun_txt, parse_rerun_file
from .html_report import render_html_report, write_html_report
from .pretty_reports import (
    render_pretty_pages,
    copy_pretty_assets,
    write_pretty_reports,
)

__all__ = [  # noqa: RUF022
    "ResultCollectorFormatter",
    "FORMATTER_SCOPED_NAME",
    "SCHEMA_VERSION",
    "ResultSetError",
    "new_result_set",
    "load_result_set",
    "merge_result_sets",
    "dump_result_set",
    "iter_scenarios",
    "capture_failure_embedding",
    "build_embedding",
    "capture_png",
    "encode_png",
    "DEFAULT_MIME_TYPE",
    "build_cucumber_json",
    "write_cucumber_json",
    "build_rerun_lines",
    "write_rerun_txt",
    "parse_rerun_file",
    "render_html_report",
    "write_html_report",
    "render_pretty_pages",
    "copy_pretty_assets",
    "write_pretty_reports",
]
