"""Service layer: the Python port of the Jenkins pipeline's three stages.

This package is the CPython/Flask port of the three stages of the source repository's Groovy
*scripted* Jenkins pipeline ``[Jenkins:L1-L17]`` - the only orchestration the source system ever
had. There, a ``node { }`` block cloned the repository, ran the Maven/Cucumber suite and published
the Cucumber report. Here each of those steps is an ordinary Python module, driven by the HTTP
surface in ``app.api`` and by the rendered report surface in ``app.web``.

Why this package holds exactly four oddly specific modules
----------------------------------------------------------
Agent Action Plan (AAP) Rule T2, verbatim:

    "One source construct, one target module. Each Jenkins stage becomes one service module;
    each Cucumber plugin becomes one reporting adapter. This makes the mapping auditable by
    inspection rather than by reading code."

The mapping is therefore one-to-one. The three stage names are quoted below exactly as the source
spells them, because that capitalisation and spacing is part of the pipeline's observable contract
- AAP section 0.8: "All three stage names, the platform dispatch, and the publisher invocation
``[Jenkins:L15]`` stay byte-identical. Only the two command strings change."

* ``clone_service.py`` ports stage ``'Clone code'`` ``[Jenkins:L2-L4]``
* ``test_runner_service.py`` ports stage ``'Run tests'`` ``[Jenkins:L6-L11]``, together with the
  Maven Surefire execution semantics ``[pom.xml:L21-L29]``
* ``report_service.py`` ports stage ``'Generate report'`` ``[Jenkins:L13-L15]``
* ``pipeline_service.py`` ports the ``node { }`` container that holds all three, in order,
  ``[Jenkins:L1-L17]``

Layering
--------
AAP Rule T7 (enterprise baseline B4), verbatim:

    "Strict one-direction internal dependencies. api → services → reporting → utils.
    Nothing under ``app/`` may import from ``tests/``."

Concretely, for every module in this package:

* MAY import: the standard library, ``app.reporting.*``, ``app.utils.*``, and configuration values
  read from the Flask application config.
* MUST NEVER import: ``app.api`` or ``app.web`` - that is the caller's direction (api -> services),
  so importing back would close a cycle - nor anything whatsoever under ``tests/``.

Runtime-only dependencies
-------------------------
No module in this package may import ``pytest``, ``pytest_bdd``, ``pytest_html``,
``pytest_metadata``, ``selenium``, ``webdriver_manager``, ``faker`` or ``requests``. Every one of
those import names belongs to ``requirements-test.txt``, never to ``requirements.txt``: the
application layer has to stay importable against the runtime manifest alone, which is exactly what
a ``python3 -c "import wsgi"`` smoke check in the deployment path proves. The BDD harness is
consequently invoked as a **subprocess** - an argument list with an explicit timeout, never a shell
string - and is never imported.

The parity contract
-------------------
``pom.xml`` is a read-only parity contract (AAP AMB-4). It is read statically for the values these
modules must reproduce; it is never compiled and never edited, and no ``.java`` file is authored
anywhere in the tree.

Deliberately preserved defects
------------------------------
``docs/migration-parity.md`` is the authoritative D1-D9 defect register. Read it before "fixing"
anything in this package: four of those entries are load-bearing here and are preserved on purpose
(AAP Rule T4 - "Defects are behavior").

* **D2** - the default tag expression ``LogOut``, ported from ``tags = "@LogOut"``
  ``[README.md:L87]``, matches no scenario in the feature file. The default run therefore selects
  zero scenarios and pytest exits ``5``, which ``test_runner_service.py`` must report as
  **SUCCESS** (validation criterion V6). A naive "non-zero means failure" mapping would make the
  port fail where the source went green.
* **D3** - the build can never fail: ``<testFailureIgnore>true</testFailureIgnore>``
  ``[pom.xml:L25]`` plus the six ``-1`` publisher thresholds ``[Jenkins:L15]``. Test failures are
  non-gating, and ``pipeline_service.py`` must generate the report **even after a failed test
  stage**, from a finally-equivalent path (validation criterion V12).
* **D7** - two contradictory clone URLs. The pipeline's
  ``https://github.com/BalamiRR/Upgenix-QA.git`` ``[Jenkins:L3]`` is the runtime default used by
  ``clone_service.py``; the README's ``https://github.com/BalamiRR/Testinium-QA.git``
  ``[README.md:L59]`` is retained in documentation only. Both strings survive and neither is
  silently unified (AAP AMB-7).
* The disabled ``<threadCount>4</threadCount>`` ``[pom.xml:L24]`` is commented out in the source and
  must stay commented out in ``test_runner_service.py``. Unlimited-thread, method-level parallelism
  (``<parallel>methods</parallel>`` with ``<useUnlimitedThreads>true</useUnlimitedThreads>``) is the
  live setting; the four-thread value is a documented, disabled tuning default.

This module contains no code
----------------------------
It is a package marker: a docstring and nothing else. Nothing is re-exported here, deliberately. A
convenience re-export would let a caller reach a service without honouring the layering above, and
it would force this marker to import all four stage modules at package-import time, eagerly pulling
in ``app.reporting`` and ``app.utils`` for no benefit. Importing ``app.services`` therefore stays
free of I/O, network access and mutation - in particular it never creates ``target/``, which AAP
section 0.6 assigns to ``app/utils/paths.py``, the ``Makefile`` test target and
``tests/conftest.py`` alone. Version metadata lives once, in ``pyproject.toml``
(``version = "1.0.0.dev0"``, the PEP 440 rendering of ``<version>1.0-SNAPSHOT</version>``
``[pom.xml:L9]``), so this package declares no version attribute of its own. Logging is configured
once, in ``app/logging_config.py``, so each service module obtains its own module-level logger
through ``logging.getLogger`` instead.
"""
