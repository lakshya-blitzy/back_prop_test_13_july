"""Report-generation layer of the Testinium-QA Python port - and its public barrel.

This package holds the event collector that records what a run did, the four
writers that turn one merged record of it into the four artifacts the source
build produced, and the screenshot helpers that put a failed scenario's
evidence into that record.  It is the port of the plugin list the Java runner
declared (``CukesRunner.java:9-14``), and specification section 0.3.3 states
the shape it keeps: *"one merged result set, four independent writers, none
aware of the others."*

``events``
    The intermediate result document and the behave formatter that fills it.
    The schema owner: every other module in this package reads that document
    and none of them defines its shape.  A custom formatter is necessary
    rather than decorative - behave's native JSON carries no per-scenario
    ``start_timestamp``, no embeddings, a ``file.py:line`` ``match.location``
    and float-second durations, and no mapping can recover a field that was
    never captured (specification section 0.6).
``screenshots``
    Capture, base64 encoding and the embedding mapping - the port of
    ``Hooks.java:15``.  Capture happens on failure only, once, before the
    driver is quit, and a capture that fails is logged and suppressed so that
    it cannot change a test outcome (deviation 19).
``cucumber_json``
    ``cucumber.json`` in the Cucumber-JVM schema.  The only artifact with a
    machine consumer in the pipeline: the Jenkins publisher reads it and
    nothing else (``Jenkins:15``).
``rerun_report``
    ``rerun.txt``, grouped one line per feature, and the parser for reading it
    back - the port of the second Java runner, whose ``features`` declaration
    names that manifest as its input (``FailedTestRunner.java:11``).
``html_report``
    ``cucumber-reports.html``: one self-contained page, no sibling asset, the
    contract ``io.cucumber:html-formatter`` 17.0.0 produced.
``pretty_reports``
    The PrettyReports tree: four overview pages, a detail page per feature and
    per tag, and the vendored asset set that lets them render offline from a
    CI workspace - the contract ``net.masterthought:cucumber-reporting`` 5.6.1
    produced.

Where the four artifacts land is not spelled out here, or anywhere in this package:
:mod:`app.utils.paths` owns every path in the port (specification section
0.4.2), each writer resolves its destination through it, and this file names
no path at all.

The export surface
------------------
This table is the package's contract.  ``app/services/report_service.py``,
``app/services/test_run_service.py``, ``app/cli.py``,
``features/environment.py`` and ``tests/*`` are written against it, so a name
may be added here only when a consumer actually imports it, and renamed only
together with its defining module.

=============================  ==================  ============================================
Name                           Defined in          Role
=============================  ==================  ============================================
``ResultCollectorFormatter``   ``events``          the formatter behave loads per worker; it
                                                   fills the document
``FORMATTER_SCOPED_NAME``      ``events``          the ``module:Class`` string passed verbatim
                                                   to behave ``-f``
``SCHEMA_VERSION``             ``events``          version of the document shape
``ResultSetError``             ``events``          a worker file absent, unreadable or not this
                                                   schema
``new_result_set``             ``events``          build an empty-by-default document
``load_result_set``            ``events``          read one worker's document back
``merge_result_sets``          ``events``          per-worker documents -> the one document the
                                                   writers consume
``dump_result_set``            ``events``          write a document as UTF-8 JSON
``iter_scenarios``             ``events``          every ``(feature, element)`` pair in a
                                                   document
``capture_failure_embedding``  ``screenshots``     a failed scenario's screenshot, as an
                                                   embedding
``build_embedding``            ``screenshots``     the embedding mapping the writers serialise
``capture_png``                ``screenshots``     the viewport as PNG bytes
``encode_png``                 ``screenshots``     base64, for inline embedding
``DEFAULT_MIME_TYPE``          ``screenshots``     ``image/png``, verbatim from
                                                   ``Hooks.java:15``
``build_cucumber_json``        ``cucumber_json``   the pure half: document -> JSON structure
``write_cucumber_json``        ``cucumber_json``   **writer 1** - ``cucumber.json``
``build_rerun_lines``          ``rerun_report``    the pure half: the manifest's lines
``write_rerun_txt``            ``rerun_report``    **writer 2** - ``rerun.txt``
``parse_rerun_file``           ``rerun_report``    read a manifest back, for ``--rerun``
``render_html_report``         ``html_report``     the pure half: the page as one string
``write_html_report``          ``html_report``     **writer 3** - ``cucumber-reports.html``
``render_pretty_pages``        ``pretty_reports``  the pure half: every page by filename
``copy_pretty_assets``         ``pretty_reports``  the vendored asset set into the emitted tree
``write_pretty_reports``       ``pretty_reports``  **writer 4** - the PrettyReports tree
=============================  ==================  ============================================

Two of those names carry more weight than their one-line role suggests.
``FORMATTER_SCOPED_NAME`` is re-exported so that ``test_run_service`` passes
the scoped name to behave without spelling the string itself - a static
configuration file cannot give each worker a distinct output path, so
``behave.ini`` declares no formatter and the name travels on the command line
instead (specification section 0.4.1).  ``capture_failure_embedding`` is the
one screenshot symbol ``features/environment.py`` needs, because that module
owns the scenario lifecycle and nothing else captures evidence.

The four writers drive in a loop
--------------------------------
Each ``write_*`` takes the merged document first and accepts ``base`` - the
directory the artifact path resolves against, defaulting to the working
directory exactly as every :mod:`app.utils.paths` accessor does - so
``report_service`` can fan one document out over the four of them uniformly:

.. code-block:: python

    from app.reporting import (
        write_cucumber_json, write_rerun_txt,
        write_html_report, write_pretty_reports,
    )

    for writer in (write_cucumber_json, write_rerun_txt,
                   write_html_report, write_pretty_reports):
        writer(result_set, base=base)      # base by keyword, always

**Pass ``base`` by keyword.**  It is the second positional parameter of three
of the writers and the third of ``write_rerun_txt``, whose second is ``path``,
so a positional call would write the rerun manifest to a directory-shaped
destination.  Each writer's single-destination override is its own affair -
``path`` for three, ``directory`` for the tree - and is not part of this loop
contract; the pure halves beside them (``build_cucumber_json``,
``build_rerun_lines``, ``render_html_report``, ``render_pretty_pages``) are
exported because they let the whole artifact contract be tested in memory,
which is how the section 0.5.1 coverage gate on this package is met without a
browser.

Import boundary
---------------
**Nothing in this package imports a service.**  Specification section 0.4.2
draws the edge as ``SV --> RP`` only: the services import the writers, never
the reverse.  If a sibling here appears to need a service, the design is
wrong, not the barrel.

Beyond that, the six modules import only the standard library, behave (the
formatter's base class), Jinja2 and MarkupSafe (the two HTML writers'
templating), :mod:`app.reporting.events` and :mod:`app.utils.paths` - the
module specification section 0.4.2 makes the sole owner of every path in the
port, which is why no path literal appears in this package or in this file.
Nothing here reaches ``app.cli``, ``app.web``, ``app.config``,
``app.automation``, ``app.pages`` or ``app.services``, and nothing here
imports Flask or Selenium.

That is load-bearing rather than tidy.  ``test_run_service`` shards scenarios
across worker processes - the port's stand-in for the source build's
``parallel=methods`` with ``useUnlimitedThreads`` (``pom.xml:22-23``) - and
each worker imports this package to record its results.  Importing
``app.reporting`` first executes the application package's ``__init__``, which
defers its Flask, blueprint and CLI imports into ``create_app()`` precisely so
that a worker which never builds a web application stays free of a web
framework.  A stray import here would undo that for every worker at once.

Import order is pinned, and it is a correctness requirement
-----------------------------------------------------------
``events`` first, then ``screenshots``, then ``cucumber_json``,
``rerun_report``, ``html_report`` and ``pretty_reports`` - foundational-first,
matching the real dependency order so that each module is fully initialised
before the ones that build on it, the same discipline
``app/automation/__init__.py`` follows for the same reason.

The hazard the order removes is worth naming precisely, because the four
writers each depend on ``events``.  Importing any submodule - say
``app.reporting.cucumber_json`` - runs this file to completion first, so with a
writer at the top of the block that writer would begin executing while this
module is still only partially initialised.  It survives that today only
because it reaches its dependency as a submodule,
``from app.reporting.events import ...``, and a submodule import tolerates a
half-built parent package; the moment a sibling instead took a name from the
barrel itself, ``from app.reporting import ...``, a non-foundational order
would raise :exc:`ImportError` on some entry points and not on others,
depending entirely on which module the process happened to import first.
Keeping ``events`` at the top means no sibling ever has to rely on that
tolerance.  The ordering below is therefore not a style preference and must
not be re-sorted.  ``screenshots`` follows ``events`` as the other module the
document's shape depends on, and itself depends on nothing in the package -
only the standard library.

Contract of this file
---------------------
It marks the package and re-exports the surface above.  It contains **no
logic** - no function, no class, no branch, no computed value, no state, no
error handling around the imports - and **no import-time side effects**:
importing ``app.reporting`` writes no artifact, creates no directory, reads no
file, starts no browser, resolves no path and configures no logging.  Logging
configuration belongs to ``app/logging_config.py``, which ``app/cli.py`` and
``create_app()`` each call once; a barrel that installed handlers would
double-install them in every worker process.  No project version is declared
here either - ``pyproject.toml`` owns it, mapping the source build's
``1.0-SNAPSHOT`` (``pom.xml:9``) to ``1.0.0.dev0``.

Deliberately withheld, so that the advertised surface stays honest
------------------------------------------------------------------
The siblings' own ``__all__`` lists are wider than this barrel, because each
publishes the helpers its own test module exercises.  What is kept out, and
why - each remains reachable through its defining module for anyone who
genuinely needs it, the precedent ``app/utils/__init__.py`` set when it
withheld ``properties.reset_cache``:

* ``html_report.build_environment`` and ``pretty_reports.build_environment``.
  Jinja environment factories are an implementation detail of the two HTML
  writers, not a service of this package.  Both writers accept an
  ``environment`` argument for the test that needs to inject one.
* ``rerun_report.RerunManifestError``, ``rerun_report.RerunEntry`` and
  ``events.attach_to_current_scenario``.  Each has a real consumer -
  ``app/cli.py`` catches the manifest error when ``--rerun`` meets a malformed
  file, which the section 0.4.1 exit contract keeps at status ``0`` with a
  message on stderr - and each consumer names the defining module, one import
  away, rather than the barrel.  They are situational rather than part of the
  fan-out surface this file advertises.
* ``events.FORMATTER_NAME``.  The short name a caller would use only after
  registering the formatter under it; the port never registers it and passes
  ``FORMATTER_SCOPED_NAME`` instead, so exporting both would advertise two
  ways to name one formatter.
* ``cucumber_json.render_cucumber_json`` and ``rerun_report.build_rerun_text``
  - string-serialising steps between the pure and impure halves already
  exported - along with every status, slug, hash, layout and asset-path helper
  the writers share with their own tests, and the ``ResultSet`` and
  ``JsonDict`` type aliases, which are annotations rather than API.
"""

# Named individually rather than star-imported so the surface is greppable and
# a typo fails loudly at import time instead of silently thinning the API.  The
# order of the six statements is pinned - see the docstring: ``events`` must be
# fully initialised before the four writers that import from it.  An import
# sorter would alphabetise them and put ``cucumber_json`` first, which is why
# the block opts out of sorting rather than being re-ordered to satisfy one.
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

# Grouped by owning module, in the order the docstring's table lists them, so
# the barrel reads as documentation of the package's surface; a single sequence
# sorted across the whole list would interleave the modules and lose that.
# Exactly these twenty-four names - every one defined by a sibling module and
# every one with a real consumer.
__all__ = [  # noqa: RUF022
    # -- app/reporting/events.py: the schema and the collector --------------
    "ResultCollectorFormatter",
    "FORMATTER_SCOPED_NAME",
    "SCHEMA_VERSION",
    "ResultSetError",
    "new_result_set",
    "load_result_set",
    "merge_result_sets",
    "dump_result_set",
    "iter_scenarios",
    # -- app/reporting/screenshots.py: failure evidence ---------------------
    "capture_failure_embedding",
    "build_embedding",
    "capture_png",
    "encode_png",
    "DEFAULT_MIME_TYPE",
    # -- app/reporting/cucumber_json.py: the JSON the publisher reads -------
    "build_cucumber_json",
    "write_cucumber_json",
    # -- app/reporting/rerun_report.py: the rerun manifest ------------------
    "build_rerun_lines",
    "write_rerun_txt",
    "parse_rerun_file",
    # -- app/reporting/html_report.py: the self-contained HTML page ---------
    "render_html_report",
    "write_html_report",
    # -- app/reporting/pretty_reports.py: the PrettyReports tree ------------
    "render_pretty_pages",
    "copy_pretty_assets",
    "write_pretty_reports",
]
