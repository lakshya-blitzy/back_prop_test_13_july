"""Package marker for ``tests``: the BDD harness and the Python test-suite tree.

The ``tests`` package holds the Python 3 / pytest-bdd replacement for the Cucumber-JVM
harness this project was migrated from. It is a *development* asset: the Flask
application under ``app`` never depends on it, and the packaging metadata keeps it out of
the distributable artifact (``pyproject.toml`` declares
``include = ["app*"]`` and ``exclude = ["tests*", ...]`` for package discovery).

Purpose of this module
======================
This file is a pure package marker. Its only job is to make ``tests`` an importable
package, which three separate mechanisms of the harness rely on:

1. Dotted-path resolution. ``tests.support``, ``tests.pages``, ``tests.step_defs``,
   ``tests.unit``, ``tests.integration`` and ``tests.parity`` become real dotted module
   paths, so sibling packages can import shared helpers by name instead of relying on
   ``rootdir`` or ``sys.path`` accidents.
2. Step discovery. pytest-bdd has no equivalent of Cucumber's ``glue`` option
   (``glue = "com/testinium/step_definitions"`` in the documented ``CukesRunner``), so
   step definitions are registered by *importing* their module: the ported runner,
   ``tests/step_defs/test_cukes_runner.py``, imports its step-definition module
   explicitly. That import resolves only because this marker exists.
3. Worker-process safety. The suite runs under pytest-xdist with ``-n logical`` -- the
   port of Maven Surefire's ``<parallel>methods</parallel>`` together with
   ``<useUnlimitedThreads>true</useUnlimitedThreads>`` -- and every xdist worker is a
   separate process that re-imports the harness from scratch. Import paths therefore
   have to be stable and package-qualified rather than incidental.

Dependency direction: one way only
==================================
``tests`` MAY import from ``app``. NOTHING under ``app`` may import from ``tests``.

That rule keeps the deployable Flask application independent of the test harness even
though both are derived from the same source specification. Inside the application the
direction is equally strict: ``api`` -> ``services`` -> ``reporting`` -> ``utils``.

This module is deliberately inert so that the reverse edge can never form: it defines
and re-exports nothing, so there is nothing here for ``app`` to be tempted to consume.

Layout of the tree
==================
``features/``
    The Gherkin specification, ``login.feature``, kept byte-identical to the block
    documented in ``README.md``. Byte-exactness is what preserves the source suite's
    behavior, catalogued defects included, so nothing may reformat this directory.
``step_defs/``
    The ported test runner (``test_cukes_runner.py``, standing in for ``CukesRunner``),
    the ported step definitions (``login_sd.py``, standing in for ``LoginSD.java``) and
    the browser fixture plus screenshot-on-failure hook that serve them.
``pages/``
    Page Object Model classes -- a base page and the login and dashboard pages -- so
    that no element locator ever appears in a step definition.
``support/``
    Harness plumbing: the WebDriver factory, the Faker-backed data factory, the
    screenshot helpers and the cached reader for ``configuration.properties``.
``unit/``
    Fast, isolated tests for the application factory, configuration precedence, the
    properties reader, the report thresholds and the platform dispatch helper.
``integration/``
    Tests that exercise the HTTP surface, the report endpoints and the ported pipeline
    stages through the Flask test client.
``parity/``
    The executable parity evidence: feature-file byte parity, dependency parity against
    the declared Maven coordinates, and preservation of each catalogued source defect.
    This is what turns "the behavior was preserved" into a claim a build can check.
``fixtures/``
    Static data, notably the golden Cucumber JSON report schema that the emitted report
    is validated against.

``features/`` and ``fixtures/`` hold data rather than importable modules, which is why
neither of them carries an ``__init__.py``.

Provenance
==========
This module has no counterpart in the migrated project: it ports no class, no
configuration block and no pipeline step. It exists purely because Python needs a
package marker for the tree above. ``README.md`` is recorded as its reference only
because that file is the primary behavioral specification from which the whole harness
was materialized -- not because any line of it is reproduced here.

Deliberate omissions
====================
Every one of the following is absent by design rather than by oversight:

* No imports. A package marker that imported ``pytest``, ``pytest_bdd``, ``selenium``,
  ``webdriver_manager``, ``faker``, ``flask`` or anything from ``app`` would turn one
  missing optional dependency into a total collection failure for every module in the
  tree. Staying inert confines such a failure to the single module that genuinely needs
  the package, and leaves the parity suite -- which needs no third-party package at all
  -- runnable under a bare interpreter.
* No re-export shims. Eagerly re-exporting submodules would make importing *any* part of
  ``tests`` execute *all* of it, which is the same trap as above, one level up.
* No ``__all__`` and no ``__version__``. Nothing is exported, and project metadata has a
  single owner: the ``[project]`` table of ``pyproject.toml``.
* No fixtures, hooks, markers or ``pytest_plugins``. All pytest wiring lives in
  ``tests/conftest.py`` and ``tests/step_defs/conftest.py``; registered marks and the
  warning filters live in ``pytest.ini``.
* No logging configuration. Handler and level setup belongs to ``app/logging_config.py``
  alone, and this module emits no output of any kind.
* No side effects. Importing this package touches no file, reads no environment variable
  and mutates no ``sys.path``. In particular it does not create the ``target/`` artifact
  root: that duty belongs to ``app/utils/paths.py``, the ``Makefile`` ``test`` target and
  ``tests/conftest.py``, and adding a fourth owner would only blur the contract.
"""
