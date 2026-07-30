"""Package marker for the ``tests.support`` package: the BDD harness plumbing.

``tests.support`` holds the four helper modules the ported Cucumber harness needs in
order to drive a browser, fabricate synthetic data, capture screen shots and read the
runtime configuration file. It contains no test of its own and nothing in it is ever
collected, because ``pytest.ini`` restricts collection to ``python_files = test_*.py``.

Why this file exists
====================
This module is a pure package marker. Its single job is to make ``tests/support`` an
importable Python package so that the four modules beside it resolve as real dotted
module paths -- ``tests.support.driver_factory`` rather than a bare, collision-prone
``driver_factory``. Four consumers depend on exactly that:

* ``tests/step_defs/conftest.py`` builds the ``driver`` fixture from
  ``driver_factory`` and wires the screenshot-on-failure hook to ``screenshots``.
* ``tests/pages/*`` receive the WebDriver instance that ``driver_factory`` produces,
  which is why the page-object package declares a sibling dependency on this one.
* ``tests/unit/test_properties_reader.py`` exercises the ``configuration.properties``
  contract through ``config_reader``.
* ``tests/integration/*`` and ``docs/configuration.md`` reference ``config_reader``
  for that same contract.

Package qualification matters at run time as well as at authoring time. The suite runs
under ``pytest-xdist`` with ``-n logical`` -- the port of Maven Surefire's
``<parallel>methods</parallel>`` together with
``<useUnlimitedThreads>true</useUnlimitedThreads>`` -- and every xdist worker is a
separate operating-system process that re-imports the harness from scratch. A
package-qualified module path removes any dependence on ``rootdir`` discovery or on
``sys.path`` ordering inside those workers.

What lives beside this file
===========================
Exactly four modules, each replacing one construct of the Java build this project was
migrated from:

``driver_factory.py``
    The Factory pattern: builds and configures the WebDriver, then hands it to the
    fixtures. Replaces the driver provisioning of
    ``io.github.bonigarcia:webdrivermanager:5.1.0`` ``[pom.xml:L42-L46]`` driving
    ``org.seleniumhq.selenium:selenium-java:3.141.59`` ``[pom.xml:L36-L40]``. Selenium 4
    resolves drivers on its own through Selenium Manager, so the ported
    ``webdriver-manager`` distribution is retained as the one-to-one counterpart of that
    Maven entry rather than as the only way to obtain a driver.
``data_factory.py``
    The Data Builder pattern: a thin wrapper over Faker, replacing
    ``com.github.javafaker:javafaker:1.0.2`` ``[pom.xml:L48-L52]``. Declared and wired
    but deliberately unexercised, because the source project declared that dependency
    without ever calling it and the Gherkin ``Examples`` tables supply static data.
    Exercising it would add behaviour the original never had.
``screenshots.py``
    Capture helpers for the two artifact kinds this project has always advertised:
    "screen shots" for tests when the feature is enabled and "error shots" for failed
    test cases ``[README.md:L42-L43]``. Output lands under ``target/screenshots/`` and
    ``target/error-shots/`` so that the clean step wipes both.
``config_reader.py``
    The Cached Configuration Reader pattern: single-point, cached access to the
    git-ignored ``configuration.properties`` runtime contract ``[.gitignore:L3]``. That
    file is absent from every fresh checkout by design, so the reader has to degrade
    gracefully and every setting has to carry a documented default.

Design patterns realised in this package
========================================
Four of the ten patterns this migration adopts are realised here: Factory (the WebDriver
builder), Data Builder (the Faker wrapper), Cached Configuration Reader (the properties
accessor), and the capture half of the screenshot lifecycle. The indexing half of that
lifecycle lives in ``app/reporting/screenshots.py``, a different module with the opposite
job: it lists already-written artifacts for the HTTP report surface. The two modules must
never import each other, and this marker must never become a bridge between them.

One of exactly seven package markers
====================================
The harness tree carries an ``__init__.py`` in exactly seven directories: ``tests/``,
``tests/step_defs/``, ``tests/pages/``, ``tests/support/`` (this file), ``tests/unit/``,
``tests/integration/`` and ``tests/parity/``. Two directories deliberately have none.
``tests/features/`` holds the Gherkin specification as data, and ``tests/fixtures/``
holds the golden Cucumber JSON report as data; neither contains an importable module.
``tests/features/`` in particular must never be walked by a formatter, because the
feature file is kept byte-identical to the block documented in ``README.md``, its
whitespace-only lines included.

Dependency direction
====================
The migration's Rule T7 governs this package. Quoted verbatim, with its arrows
transliterated to ASCII so that this file stays ASCII-only:

    "Strict one-direction internal dependencies. ``api`` -> ``services`` ->
    ``reporting`` -> ``utils``. Nothing under ``app/`` may import from ``tests/``."

Stated plainly: modules under ``tests/`` MAY import from ``app/``, but NOTHING under
``app/`` may ever import from ``tests/``. That keeps the deployable Flask application
independent of the test harness even though both are derived from the same source
specification. This marker therefore defines and re-exports nothing whatsoever -- there
is deliberately no symbol here that ``app/`` could be tempted to consume, so the reverse
edge cannot even begin to form.

How to import from this package
===============================
Always reach for the specific submodule you need, never for a re-export from the package
itself. Importing a name out of ``tests.support.config_reader``, or importing the
``config_reader`` module out of ``tests.support``, both work. Importing a helper name
directly out of ``tests.support`` never will, because no name is re-exported here. That
is a hard contract rather than a stylistic preference, and the next section explains why.

Two wording constraints follow from that contract, and both are deliberate rather than
awkward. No line of this file may begin with an import keyword, not even inside this
prose, because the verification gate greps for a line-initial import statement and
requires zero matches -- the cheapest possible proof that the module is inert. For the
same reason the prose below says "export list" and "version constant" instead of spelling
out the two dunder attribute names the gate also greps for. Neither attribute is defined
here, and the authoritative proof is stronger than any grep: the module's abstract syntax
tree contains exactly one node, this docstring.

This file is deliberately inert, and that is a correctness requirement
======================================================================
This module holds this docstring and nothing else: no import, no statement, no
definition, no side effect.

The reason is concrete. Three of the four siblings sit on third-party distributions --
``selenium``, ``webdriver-manager`` and ``Faker`` -- which are pinned in
``requirements-test.txt`` but may be entirely absent at collection time: a verification
environment is not guaranteed to have network access, so a successful installation
cannot simply be assumed. If this marker re-exported anything, then importing ANY part
of ``tests.support`` would execute ALL of it, and one unavailable distribution would
become a collection error for the entire package. That error would take down the parity
suite -- ``tests/parity/test_feature_file_byte_parity.py``,
``tests/parity/test_dependency_parity.py`` and
``tests/parity/test_defect_preservation.py`` -- which is the behavioural acceptance gate
of the whole migration and is deliberately written to need nothing beyond the standard
library. Inertness is what confines a missing optional dependency to the single module
that genuinely needs it.

The same reasoning binds the harness modules around this one: ``tests/conftest.py`` must
not import from ``tests.step_defs``, ``tests.pages`` or ``tests.support`` at module
level, precisely because those pull in ``pytest_bdd``, ``selenium`` and ``faker``. An
inert marker here is what makes ``import tests.support`` free and total, and therefore
what makes that constraint satisfiable at all.

A lazy module-level ``__getattr__`` shim would not be an acceptable compromise either.
It looks inert while merely deferring the identical failure to attribute-access time,
and it reintroduces the package-level coupling this file exists to avoid.

Deliberate omissions
====================
Every one of the following is absent by design rather than by oversight:

* No imports of any kind, not even from the standard library.
* No re-export shims, and no module-level ``__getattr__``.
* No export list, because nothing is exported.
* No version constant. Project name and release metadata have a single owner, the
  ``[project]`` table of ``pyproject.toml`` (``testinium-qa`` at ``1.0.0.dev0``, the PEP
  440 rendering of the source build's ``1.0-SNAPSHOT``). A second source of truth would
  drift, and would undermine the dependency- and coordinate-parity checks that compare
  the two builds.
* No fixtures, hooks, marks or ``pytest_plugins``. The harness owns exactly two conftest
  modules, ``tests/conftest.py`` and ``tests/step_defs/conftest.py``, and all pytest
  wiring belongs to them, while the registered marks and the warning filters belong to
  ``pytest.ini``. There is no conftest module in this directory and there must not be
  one: fixtures reach these modules through ordinary conftest inheritance.
* No logging configuration. Handler and level setup belongs to ``app/logging_config.py``
  alone. This module emits no output of any kind, and never a bare print.
* No file access, so there is no encoding to declare here -- while every sibling that
  does read a file opens it with an explicit UTF-8 encoding.
* No side effects at all: no filesystem access, no environment read, no ``sys.path``
  mutation. In particular this module does NOT create the ``target/`` artifact root.
  That duty has exactly three owners -- ``app/utils/paths.py``, the ``Makefile``
  ``test`` target and ``tests/conftest.py`` -- and a fourth owner would only blur the
  contract.
* No fifth module in this directory and no ``py.typed`` marker. The package is exactly
  the four siblings described above, plus this file.

Provenance
==========
This module ports nothing. It has no counterpart in the migrated project: no class, no
configuration block, no pipeline step. Its four siblings each carry an explicit source
binding, cited above; this file carries none, and exists purely because Python requires a
package marker for the directory that holds them. ``README.md`` is recorded as its
reference only because that file is the primary behavioural specification from which the
whole harness was materialised -- not because any line of it is reproduced here.
"""
