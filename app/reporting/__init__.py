"""Reporting adapters: the report-artifact layer of the Python/Flask port.

This package is the Adapter/Gateway layer of the application. It reproduces in
Python every report artifact the source Java/Maven Cucumber build published, so
that consumers of the ported system still find the same files, in the same
places, with the same shapes.

Its shape follows AAP Rule T2 exactly: "One source construct, one target module.
Each Jenkins stage becomes one service module; each Cucumber plugin becomes one
reporting adapter. This makes the mapping auditable by inspection rather than by
reading code."

Module inventory
----------------
One module per source construct. The four plugin strings below are ``plugin``
entries of the ``@CucumberOptions`` block on the documented ``CukesRunner`` in
``README.md``, reproduced byte-exactly. They are cited by literal value and
never by line number, because the line numbers recorded for these four strings
in the migration plan are off by one; the literal is the only safe citation.

* ``thresholds.py`` -- the frozen constants of the ``cucumber`` publisher
  invocation on ``Jenkins`` line 15: the six ``-1`` report thresholds,
  ``sortingMethod: 'ALPHABETICAL'`` and ``fileIncludePattern: '**/*.json'``. It
  publishes ``PUBLISHER_THRESHOLDS``, which ``app/services/report_service.py``
  consumes as ``from app.reporting.thresholds import PUBLISHER_THRESHOLDS``.
* ``cucumber_json.py`` -- ports ``"json:target/cucumber.json"``.
* ``html_report.py`` -- ports ``"html:target/cucumber-reports.html"``.
* ``rerun_report.py`` -- ports ``"rerun:target/rerun.txt"``.
* ``pretty_reports.py`` -- ports
  ``"me.jvt.cucumber.report.PrettyReports:target/cucumber"``.
* ``screenshots.py`` -- ports the README prose promising ``screen shots`` for
  tests when they are enabled and ``error shots`` for failed test cases.

The package is flat and closed at those seven modules: this marker plus the six
above. No aggregator, exporter or sub-package belongs here, because a further
artifact would be a new feature and the port adds none.

Layering contract
-----------------
AAP Rule T7 fixes one dependency direction, ``api -> services -> reporting ->
utils``. This package may therefore import the standard library and
``app.utils.*``, and nothing else. It must never import ``app.services``,
``app.api``, ``app.web``, the ``app`` package root or ``app.config``: each of
those depends on this package directly or transitively, so importing one back
would create a cycle. Rule T7's other half binds just as tightly, in the words
of the plan: "tests/ may import from app/; nothing under app/ may import from
tests/". ``scripts/`` is out of bounds for the same reason.

The adapters are consequently framework-agnostic: no module-level Flask object
and no use of ``current_app`` or ``request`` (``app/__init__.py`` owns the Flask
wiring), no HTTP responses (``app/errors.py`` owns those), no process spawning,
and no logging configuration (``app/logging_config.py`` owns that; modules here
only emit records). Artifact locations come from ``app/utils/paths.py``, which
also owns creating the ``target`` tree.

Runtime-only dependency rule
----------------------------
This is the sharpest trap in the package. ``app/config.py`` imports
``app/reporting/thresholds.py``, so the deployed import chain runs ``wsgi.py``
-> ``app/__init__.py`` (``create_app``) -> ``app/config.py`` ->
``app/reporting/thresholds.py``. The container image is built from
``requirements.txt`` alone, so every module here must import cleanly against
only the eleven runtime pins: Flask, Werkzeug, Jinja2, MarkupSafe, itsdangerous,
click, blinker, gunicorn, python-dotenv, flask-cors and pydantic.

Never import ``pytest``, ``pytest_bdd``, ``pytest_html``, ``pytest_metadata``,
``selenium``, ``webdriver_manager``, ``faker`` or ``requests`` from this
package. Those are harness distributions, declared in ``requirements-test.txt``
and absent from the deployed image. These adapters read the artifacts those
tools produce; they never invoke the tools.

About this module
-----------------
This file is a package marker and nothing more. It exists so that
``app.reporting`` is an importable package and the mandated import path
``from app.reporting.thresholds import PUBLISHER_THRESHOLDS`` resolves for
``app/config.py`` and ``app/services/report_service.py``. It re-exports nothing,
imports nothing, declares neither an export list nor a version attribute of its
own (``pyproject.toml`` owns the version), and has no side effects whatsoever:
nothing is created, read or inspected on import. A convenience re-export would
hand the publisher constants a second import path, and those values are required
to exist in exactly one place.

``docs/migration-parity.md`` is the authoritative register of the source
system's preserved defects D1 through D9, several of which are observable in the
artifacts these adapters emit, and it records the configuration switch that opts
into each available fix.
"""
