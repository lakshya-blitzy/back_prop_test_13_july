"""Package marker for ``tests.step_defs``: the ported BDD runner and its step definitions.

``tests.step_defs`` is the direct materialization of the only executable specification
this project possesses. The migrated repository declared a Cucumber-JVM runner and a
step-definition class, printed the runner's configuration verbatim in ``README.md``, and
then never committed either file. The modules in this package are their Python
counterparts, and one of them is the single module the test session actually collects,
which makes this the most consequential directory in the harness.

This module itself is a pure package marker. Its only job is to make ``tests.step_defs``
an importable package so that three dotted paths resolve as real modules:
``tests.step_defs.test_cukes_runner``, ``tests.step_defs.login_sd`` and
``tests.step_defs.conftest``. The ported runner reaches its step definitions through the
second of those paths, and the parity and integration suites reach the runner through the
first.

Why this module is inert
========================
It holds a docstring and nothing else: no import statement, no assignment, no definition,
no call. That is a deliberate engineering decision and the single most important thing to
understand before editing this file.

The harness depends on distributions that are not guaranteed to be present when a session
is collected -- pytest-bdd, selenium, webdriver-manager, pytest-xdist, pytest-html and the
synthetic-data generator among them -- and not every environment this tree is collected in
permits installing them. A package marker that re-exported its submodules for convenience,
however small the shim, would make importing *any* part of ``tests.step_defs`` execute
*all* of it: the browser stack and the Gherkin engine would be dragged in by callers that
need neither. One absent distribution would then stop being a skip in a single module and
become a collection error for the whole package -- and the parity suite, which is the
acceptance gate of this migration and needs no third-party package at all, would fall with
it.

Inertness is therefore the feature, not an oversight. Keeping this marker free of code
confines a missing distribution to the one module that genuinely requires it, leaves each
sibling free to guard its own imports, and makes ``import tests.step_defs`` total: it
cannot fail, because there is nothing in it to fail. Resist the temptation to tidy this
file up with convenience re-exports. That tidying is precisely the failure mode the file
exists to prevent.

The modules this package holds
==============================
``test_cukes_runner.py`` -- THE COLLECTED TARGET
    The port of the ``CukesRunner`` class documented in ``README.md`` L71-L93. pytest-bdd
    cannot execute a ``.feature`` file directly: something has to bind the scenarios to
    generated test functions, and the module that performs that binding is the one that
    gets collected. Where the Java build selected its runner by filename, matching the
    Surefire include glob ``**/CukesRunner*.java`` (``pom.xml`` L27), this suite selects
    it by collection -- which makes the ``test_`` prefix load bearing. Rename the file to
    anything outside the configured test-file pattern and the whole suite silently
    disappears, reporting success over zero scenarios.
``login_sd.py``
    The port of ``/step_definitions/LoginSD.java`` (``README.md`` L97): the
    given/when/then bindings. They carry no element locator of their own and delegate
    every browser interaction to the page objects of ``tests.pages``.
``conftest.py``
    Additive plumbing with no counterpart in the migrated project: the browser-driver and
    page-object providers that pytest injects into the step functions, plus the
    screenshot-on-failure hook that preserves the documented artifact behaviour -- "screen
    shots" for runs that enable them and "error shots" for failed test cases
    (``README.md`` L42-L43).

Constructs that never existed
=============================
``CukesRunner``, ``LoginSD.java`` and the glue package ``com/testinium/step_definitions``
(``README.md`` L85) are named by the migrated project's own documentation and build
configuration, yet no branch of this repository's history ever contained them. They are
specifications to materialize in Python, not sources to read, and nothing may be claimed
about their content beyond what ``README.md`` prints verbatim. The same applies to
Cucumber's ``glue`` option itself: pytest-bdd offers no equivalent, so step registration
happens through ordinary module import -- which is the entire reason this marker has to
exist.

Where this package sits in the tree
===================================
This is one of exactly seven package markers under ``tests``: the harness root plus the
``step_defs``, ``pages``, ``support``, ``unit``, ``integration`` and ``parity``
sub-packages. Two further directories deliberately have no marker at all, because they
hold data rather than importable modules: ``features/``, which carries the Gherkin
specification kept byte-identical to the block documented in ``README.md``, and the
golden-report directory holding the expected Cucumber JSON that the emitted report is
validated against.

Dependency direction: one way only
==================================
Transformation rule T7 governs the whole tree. Quoted here with its arrows rendered in
ASCII, because this file stays ASCII-only: "Strict one-direction internal dependencies.
api -> services -> reporting -> utils. Nothing under ``app/`` may import from ``tests/``."

The harness may depend on the application -- guarded, since reaching any ``app`` submodule
executes the application factory first and that requires Flask -- but the application may
never depend on the harness. This marker keeps the reverse edge impossible to form: it
defines and re-exports nothing, so there is nothing here for application code to be
tempted to consume.

Provenance
==========
This module ports no class, no configuration block and no pipeline stage. Unlike its three
siblings it has no source construct of its own; it exists purely because Python needs a
marker for a package whose submodules are addressed by dotted path. ``README.md`` is
recorded as its reference only because that file is the primary behavioural specification
the whole harness was materialized from -- not because any line of it is reproduced here.

Deliberate omissions
====================
Every item below is absent by design, and each has an owner elsewhere:

* No imports and no re-export shims, for the reason set out above. Consumers reach for the
  specific submodule they need -- the runner imports its step definitions directly -- and
  never a symbol re-exported from this package.
* No export list. Nothing is exported, and the pattern is doubly wrong in this directory:
  pytest-bdd names the step helpers it generates after the parsed step text, so those names
  contain spaces and quotation marks and could never be enumerated anyway.
* No version constant. Project metadata has a single owner, the ``[project]`` table of
  ``pyproject.toml``.
* No providers, no hooks, no marks, no plugin declarations. Session wiring belongs to the
  two conftest modules of this tree -- the harness root's and this package's -- and the
  registered marks and warning filters belong to the root ``pytest.ini``.
* No logging configuration and no output of any kind. Handler and level setup is the sole
  responsibility of the application's logging module.
* No side effects. Importing this package touches no file, reads no environment variable
  and mutates no import path. In particular it does not create the ``target/`` artifact
  root: that duty already has three named owners -- the application's path helper, the
  Makefile test target and the harness root's conftest -- and a fourth would only blur the
  contract the build checks.
* No fifth file in this directory and no subdirectory. The package is exactly this marker
  plus the three modules described above.
"""
