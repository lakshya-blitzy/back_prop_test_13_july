"""Package marker for the ``tests.unit`` package.

This module exists for exactly one reason: to make ``tests/unit`` a real, importable Python
package, so that every unit suite beside it resolves as a proper dotted module path --
``tests.unit.test_config`` rather than a bare top-level ``test_config``. That distinction is
load-bearing rather than cosmetic, because ``tests/unit``, ``tests/integration`` and
``tests/parity`` are peer packages whose modules would otherwise be imported under colliding
top-level names during collection.

Scope of this package
=====================

``tests.unit`` holds the unit suites over the ported application modules. Each sibling asserts
one narrowly defined contract:

* ``test_app_factory.py`` -- the application factory builds an isolated application instance and
  registers both blueprints together with the error handlers. The handlers are asserted to be
  registered at application level, because a 404 or 405 handler attached to a blueprint is never
  invoked for a URL that matches no route at all.
* ``test_config.py`` -- the configuration precedence chain (explicit constructor argument, then
  environment variable, then ``.env`` file, then ``configuration.properties``, then hard-coded
  default) and that every hard-coded default equals the value carried over from the source build.
* ``test_properties_reader.py`` -- parsing of the Java ``.properties`` format used by the runtime
  configuration file, and graceful behaviour when that file is absent. That file is deliberately
  git-ignored, so it is never present in a fresh checkout and every setting must stay optional.
* ``test_thresholds_parity.py`` -- the six report-publisher thresholds all resolve to ``-1``, the
  report sort order resolves to ``ALPHABETICAL``, and the report include glob resolves to the
  literal ``**/*.json`` value that the pipeline's report publisher consumes.
* ``test_platform_exec.py`` -- the platform dispatch that ports the pipeline's ``isUnix()``
  branch, selecting the shell command list on POSIX hosts and the batch command list elsewhere.
* ``test_cucumber_json_schema.py`` -- the emitted Cucumber JSON matches the golden key sets at
  feature, scenario and step level, and tag values are emitted without a leading ``@``.
* ``test_paths.py`` -- the ``target/`` artifact layout, including that the tree is created before
  a run: the Cucumber JSON writer does not create its own parent directory, so an absent
  ``target/`` would abort the session at report-writing time.
* ``test_markers.py`` -- marker registration and warning hygiene: every Gherkin tag is
  registered as a marker, so a full run emits no unknown-mark warnings while any genuinely new
  warning still surfaces.

Dependency direction
====================

Modules under ``tests`` may import from ``app``; nothing under ``app`` may ever import from
``tests``. That edge is strictly one-way, so the deployable application stays independent of the
test harness even though both are derived from the same source specification.

Deliberate inertness
====================

This module is intentionally empty apart from this docstring. It imports nothing, defines
nothing, exports nothing and has no side effect of any kind -- no file system access, no
environment read, no import-path manipulation -- so importing ``tests.unit`` is free and total.

That is a correctness requirement rather than tidiness. An eager re-export here would make
importing any single module in this package execute all of them, which would in turn pull the
whole application package -- and therefore Flask, plus every other third-party runtime
dependency behind it -- into every import of the package. One unavailable distribution would
then stop the entire session from being collected, including the parity suite that acts as the
behavioural acceptance gate.

For the same reason there is no ``conftest.py`` beside this file, and there must not be one. All
test-framework wiring lives in the two conftest modules the harness does own, at
``tests/conftest.py`` and ``tests/step_defs/conftest.py``, plus the root ``pytest.ini`` that is
the single source of test configuration. Project name and release metadata are owned solely by
``pyproject.toml`` and are deliberately not restated here, so exactly one source of truth exists
for them.

Provenance
==========

This file has no counterpart in the source project. That project is a Java/Maven
Selenium-Cucumber automation skeleton whose only tracked files are its build descriptor, its
pipeline definition, its ignore rules and its README -- it has never contained a committed test
tree of any kind. ``README.md`` is recorded as the origin of this harness because it is the only
behavioural specification the project possesses, but no line of it is reproduced in this module.
This file exists purely as a Python packaging necessity.
"""
