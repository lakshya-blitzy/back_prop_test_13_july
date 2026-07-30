"""Package marker for ``app.utils``, the bottom layer of the application.

This module contains no code. It exists for exactly one reason: to make
``app/utils`` an importable Python package, so that the rest of the application
can write ``from app.utils.paths import ...``-style imports. That is what
satisfies the ``config -> utils``, ``services -> utils`` and
``reporting -> utils`` edges of the internal dependency graph.

Layering. ``app.utils`` is the terminal node of the project's strict
one-direction dependency chain, ``api -> services -> reporting -> utils``.
Everything else under ``app/`` imports *from* this package; this package imports
*from* nothing inside ``app/``. Every module here consequently depends on the
Python standard library alone: no first-party module, no third-party
distribution, and never anything under ``tests/`` or ``scripts/``. Keeping the
bottom layer dependency-free is what lets the deployable application stand on
its own - in particular independent of the pytest / pytest-bdd / Selenium
harness, which the deployed container never installs - even though the
application and the harness are derived from the same source specification.

Contents. The package holds exactly three modules, each porting one construct of
the Java/Maven original:

``paths.py``
    The ``target/`` artifact layout. It centralises the report paths declared by
    the four Cucumber plugin entries - ``html:target/cucumber-reports.html``,
    ``json:target/cucumber.json``, ``rerun:target/rerun.txt`` and
    ``me.jvt.cucumber.report.PrettyReports:target/cucumber``
    [README.md:L79-L82] - together with the ``screen shots`` and ``error shots``
    directories described at [README.md:L42-L43]. The Maven-flavoured
    ``target/`` name is retained deliberately, so that the CI publisher's
    ``**/*.json`` include pattern keeps matching without any change. This module
    also owns creating that directory tree, because the Cucumber JSON writer
    does not create its own parent directory.

``properties.py``
    A standard-library reader for the Java ``.properties`` runtime-configuration
    contract, ``configuration.properties``, which stays git-ignored
    [.gitignore:L3] and is therefore always optional at run time: no fresh
    checkout contains it, so each setting it can supply also carries a
    documented default elsewhere.

``platform_exec.py``
    The pipeline's platform dispatch. The Groovy branch
    ``if (isUnix()) { sh "..." } else { bat "..." }`` [Jenkins:L7-L11] becomes a
    ``platform.system()`` decision that yields an explicit subprocess argument
    list, never a string handed to a shell.

Those three modules are mutually independent - none imports another - and this
marker deliberately re-exports nothing from them, so it defines no ``__all__``.
Consumers import the specific submodule they need. Eager re-exports would make
importing any one submodule execute all three, coupling them for no benefit and
creating a latent import-order hazard.

Unlike its three siblings, this file ports no source construct of its own: it is
a Python packaging necessity with no Java, Groovy or Gherkin counterpart in the
migrated project, and nothing in it derives from the behavioural specification
beyond the module citations above. Accordingly it declares no ``__version__`` -
project version metadata is owned by ``pyproject.toml``, which renders Maven's
``1.0-SNAPSHOT`` [pom.xml:L7-L9] as the PEP 440 version ``1.0.0.dev0`` - and it
configures no logging, which belongs exclusively to ``app/logging_config.py``.
It performs no side effect of any kind: no filesystem access, no directory
creation, no environment read, no I/O. In particular it does not create
``target/``; that responsibility belongs to ``app/utils/paths.py``, the
``Makefile`` ``test`` target and ``tests/conftest.py``. Importing this package
is therefore free and cannot fail.
"""
