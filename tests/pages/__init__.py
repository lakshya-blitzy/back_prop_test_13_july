"""Page Object Model layer of the ported BDD harness.

``tests.pages`` realises the Page Object Model pattern adopted for this migration:
"Encapsulates locators so step definitions contain no selectors." Three modules live here,
and no fourth belongs in this directory:

* ``base_page.py`` -- the Page Object base class; it owns ALL waiting, EXPLICIT waits only.
* ``login_page.py`` -- locators and actions for the login steps derived from the Gherkin
  block documented in ``README.md`` (lines 104-148).
* ``dashboard_page.py`` -- the dashboard assertion surface for the
  ``Then User should see the dashboard`` step.

This module is DELIBERATELY INERT: no imports, no re-exports, no export list, nothing
executable at all. Those three modules reference Selenium, an optional distribution that
can be absent from a network-less environment, and an eager re-export here would make
importing this package execute all of them -- turning one missing distribution into a
collection error for the whole parity suite, this migration's acceptance gate. Import the
concrete module instead, ``from tests.pages.login_page import ...``, and never a name
re-exported from the package itself.

Page objects receive an ALREADY-CONSTRUCTED ``WebDriver`` by constructor injection from
``tests/support/driver_factory.py``, the Factory pattern that replaces the Java project's
WebDriverManager provisioning; no module here ever builds, configures or quits a driver.

The application under test is external, unmodifiable and unreachable from CI, so the
locators in ``login_page.py`` and ``dashboard_page.py`` are best-effort derivations from the
Gherkin step text which the owners must confirm against the live application. Parity
evidence in this project is structural -- collection counts, artifact production, schema
conformance, constant equality, exit-code mapping -- never end-to-end browser assertions.

This is one of exactly seven ``__init__.py`` files under ``tests/``: ``tests/``,
``tests/step_defs/``, ``tests/pages/``, ``tests/support/``, ``tests/unit/``,
``tests/integration/`` and ``tests/parity/``. Neither ``tests/features/`` nor
``tests/fixtures/`` has one, nor should ever get one: they hold data -- the Gherkin
specification and the golden Cucumber JSON report -- resolved by path, not imported.

See ``docs/testing.md`` for authoring and running scenarios, and ``docs/migration-parity.md``
for the authoritative register of preserved defects.
"""
