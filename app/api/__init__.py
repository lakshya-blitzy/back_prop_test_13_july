"""HTTP API blueprint: ``api_bp``, mounted at ``/api/v1``.

This package publishes exactly one symbol, the Flask blueprint ``api_bp``. It is
the Python port of the three stages of the source repository's Groovy *scripted*
Jenkins pipeline, ``[Jenkins:L2-L16]`` - the only orchestration that system ever
had. There, a ``node { }`` block cloned the repository, ran the Maven/Cucumber
suite and published the Cucumber report, and the sole way to reach any of it was
to trigger a Jenkins job. Here each stage is an HTTP-addressable endpoint.

Why an HTTP surface exists at all
---------------------------------
The source system had no HTTP server whatsoever, so the route list could not be
ported - it had to be derived. AAP section 0.8 is binding on the framework
choice: "'a Python 3 Flask application' - Flask is mandatory, not one option
among several. This is why an HTTP surface exists at all in a system that had
none (AMB-6), and why the application factory and blueprint patterns are
structural rather than stylistic."

That derivation was mechanical rather than creative, and the constraint is what
keeps this package honest. AAP section 0.3.1: "This is the smallest HTTP surface
that makes the existing behavior addressable without inventing capability. Every
route traces to a named stage or artifact."

One source construct, one target module
---------------------------------------
AAP Rule T2, verbatim, because it fixes the shape of the whole ``app/`` tree:

    "One source construct, one target module. Each Jenkins stage becomes one
    service module; each Cucumber plugin becomes one reporting adapter. This
    makes the mapping auditable by inspection rather than by reading code."

The three stage names are quoted below exactly as the pipeline spells them -
that capitalisation, that single space - because AAP section 0.8 requires that
"All three stage names, the platform dispatch, and the publisher invocation
``[Jenkins:L15]`` stay byte-identical. Only the two command strings change."

* Stage ``'Clone code'`` ``[Jenkins:L2-L4]`` is reached by ``POST /api/v1/clone``
  and delegates to ``app.services.clone_service``.
* Stage ``'Run tests'`` ``[Jenkins:L6-L11]`` is reached by ``POST /api/v1/runs``
  and delegates to ``app.services.test_runner_service``, which also carries the
  Maven Surefire execution semantics ``[pom.xml:L21-L29]``.
* Stage ``'Generate report'`` ``[Jenkins:L13-L15]`` is reached by
  ``POST /api/v1/reports`` and delegates to ``app.services.report_service``,
  which applies the publisher settings of ``[Jenkins:L15]``.

Route inventory - closed at eight
---------------------------------
The eight rules below are rows 2-9 of the ten-row HTTP surface table in AAP
section 0.3.1. Every one of them is defined in :mod:`app.api.routes`; not one is
defined in this module, which owns the blueprint object and nothing else.

* ``GET /api/v1/config`` - configuration introspection: the clone URL, the tag
  expression and the publisher thresholds (feature F-012).
* ``POST /api/v1/clone`` - stage ``'Clone code'`` ``[Jenkins:L2-L4]`` (F-010).
* ``POST /api/v1/runs`` - stage ``'Run tests'`` ``[Jenkins:L6-L11]`` (F-002,
  F-003).
* ``GET /api/v1/runs/<run_id>`` - run status and summary, derived from the JSON
  report (F-009).
* ``POST /api/v1/reports`` - stage ``'Generate report'`` ``[Jenkins:L13-L15]``
  (F-009, F-010).
* ``GET /api/v1/reports/<run_id>/cucumber.json`` - serves the JSON report
  (F-009).
* ``GET /api/v1/reports/<run_id>/rerun.txt`` - serves the rerun manifest
  (F-009).
* ``GET /api/v1/reports/<run_id>/screenshots`` - serves the screen shots and the
  error shots promised by ``[README.md:L42-L43]`` (F-009).

The remaining two rows of that table belong elsewhere, deliberately.
``GET /health`` is registered directly on the application inside
``create_app()`` (:mod:`app`), and ``GET /`` together with ``GET /reports``
belong to the root-mounted ``web_bp`` in :mod:`app.web.routes`.

The set is CLOSED at eight. Some prose in the migration plan speaks of "nine"
routes for this blueprint; the enumerated table is authoritative at eight, and
there is no phantom gap to close. A ninth ``/api/v1`` rule would additionally
break the factory's own check that the application exposes exactly the ten
documented rules plus the automatic ``/static/<path:filename>`` Flask adds.

``GET /health`` is not owned here
---------------------------------
It is the single additive entry in the whole HTTP surface - "only the health
endpoint is additive, and it exists because a deployable service needs a
liveness probe" - and :mod:`app` registers it on the application object itself,
inside ``create_app()``. Two independent reasons keep it out of this package: its
path is ``/health``, not ``/api/v1/health``, so it cannot sit beneath this
blueprint's prefix at all; and this package is scoped to the three ported stages
plus the report-retrieval endpoints, which a liveness probe is not. Registering
it here would add an eleventh application rule and fail the factory's
exact-route-map assertion. This paragraph exists so the omission reads as the
decision it is rather than as an oversight to be corrected.

No error handler and no request hook are registered here
--------------------------------------------------------
Also a decision, and a researched one. AAP section 0.3.2: "Blueprint-level 404
and 405 handlers are **not** invoked for invalid URLs, because a blueprint does
not own a URL space. This is why ``app/errors.py`` registers handlers at
application level, with per-blueprint handlers reserved for errors raised inside
blueprint views. Had this been overlooked, unknown routes would have produced
Flask's default HTML error page instead of the intended structured response."

:mod:`app.errors` therefore owns every 404, 405 and 500 handler at application
scope, answering with JSON for ``/api/v1`` paths and with a rendered error page
elsewhere. Validation criterion V11 grades precisely that: an unknown route must
be answered "from the application-level error handler (registered on the app, not
on a blueprint, so it actually fires)", and the handler registry is inspected to
confirm nothing is registered under a blueprint key. Nor is any request,
teardown or template-context hook installed here: none is asked for anywhere in
the plan, and AAP section 0.8 is binding - "No feature may be dropped, and none
may be added."

Layering
--------
AAP Rule T7, verbatim (enterprise baseline B4):

    "Strict one-direction internal dependencies. api → services → reporting →
    utils. Nothing under ``app/`` may import from ``tests/``."

Concretely, for every module in this package:

* MAY import: the standard library, ``flask``, ``pydantic`` and
  ``app.services.*``; and, where genuinely needed, ``app.reporting.*`` and
  ``app.utils.*``.
* MUST NEVER import: ``app.web``, a sibling at the same architectural layer
  rather than something below it, nor anything whatsoever under ``tests/`` or
  ``scripts/``.

This module itself imports far less than that allowance: ``flask`` at the top and
its own ``routes`` submodule at the bottom, nothing more. In particular it must
never reach back up into :mod:`app`, ``app.config``, ``app.errors``,
``app.extensions`` or ``app.logging_config``, because the factory imports this
package and any of those would close an import cycle. AAP section 0.4.2 states
the mechanism that keeps the cycle open: "Blueprint objects are imported inside
``create_app()``, after construction, to prevent circular imports." Importing
this package therefore has to stay cheap and side-effect free - no I/O, no
subprocess, no network access, no configuration reading and no directory
creation happens at import time.

Runtime-only dependencies
-------------------------
No module in this package may import ``pytest``, ``pytest_bdd``, ``pytest_html``,
``pytest_metadata``, ``xdist``, ``selenium``, ``webdriver_manager``, ``faker`` or
``requests``. Every one of those import names is declared in
``requirements-test.txt`` and never in ``requirements.txt``, and the deployed
container installs the runtime manifest alone. The import chain that has to hold
there is ``wsgi.py``, then :mod:`app`, then :mod:`app.api`, then
:mod:`app.api.routes`; one stray harness import anywhere along it makes the
container fail to start. The BDD harness is consequently driven as a subprocess
by ``app.services.test_runner_service`` - an argument list with an explicit
timeout, never a shell string - and is never imported.

The parity contract
-------------------
``pom.xml`` is a read-only parity contract (AAP AMB-4). It is read statically as
the authoritative record of the versions, plugin settings and thresholds this
port reproduces; it is never compiled and never edited, and no ``.java`` file is
authored anywhere in the tree.

Further reading
---------------
``docs/api.md`` is the route reference for the eight endpoints above.
``docs/migration-parity.md`` is the authoritative D1-D9 defect register, and two
of its entries govern what these endpoints are allowed to report (AAP Rule T4,
"Defects are behavior"):

* **D2** - the default tag expression ``LogOut``, ported from
  ``tags = "@LogOut"`` ``[README.md:L87]``, matches no scenario in the feature
  file. A default run therefore selects zero scenarios, and that has to be
  reported as a SUCCESSFUL zero-scenario run rather than as an error (validation
  criterion V6). These endpoints must not invent a failure the source system
  never had.
* **D3** - the pipeline is non-gating:
  ``<testFailureIgnore>true</testFailureIgnore>`` ``[pom.xml:L25]`` together with
  the six ``-1`` publisher thresholds ``[Jenkins:L15]``. Test failures never fail
  the run, and report generation happens even after a failed test stage
  (validation criterion V12).

Both defects are preserved on purpose. Read that register before "fixing"
anything these endpoints report.
"""

from flask import Blueprint

# The single name this package publishes. ``create_app()`` performs
# ``from app.api import api_bp`` inside its own body, and app/api/routes.py
# performs the same import in order to decorate the blueprint, so the identifier
# is part of the contract and must not be renamed or aliased. Nothing else is
# exported: a convenience re-export of a service, reporting or utility symbol
# would let a consumer bypass the layering Rule T7 establishes, and no version
# attribute is declared here because version metadata lives once, in
# pyproject.toml (``version = "1.0.0.dev0"``, the PEP 440 rendering of
# ``<version>1.0-SNAPSHOT</version>`` ``[pom.xml:L9]``).
__all__ = ["api_bp"]

# ``url_prefix`` IS DECLARED HERE, AND HERE ONLY.
#
# This module is the single authoritative place that fixes the mount point.
# ``create_app()`` registers this blueprint WITHOUT re-specifying ``url_prefix``,
# because Flask concatenates a prefix supplied at registration time onto the one
# the blueprint already carries: naming it in both places would mount every rule
# at ``/api/v1/api/v1/...`` and break the entire surface. If the mount point ever
# has to move, the line below is the only line that changes.
#
# ``"api"`` is both the ``url_for`` namespace (``api.<endpoint>``) and the key
# under which the factory records this blueprint, so it is deliberately stable.
# ``template_folder`` and ``static_folder`` are omitted on purpose: this
# blueprint answers with JSON and with artifacts read from the ephemeral report
# root, it renders no HTML of its own - that is the root-mounted ``web_bp``
# together with ``app/templates`` - and it ships no static asset of its own.
api_bp: Blueprint = Blueprint("api", __name__, url_prefix="/api/v1")

# Bottom import -- REQUIRED, not optional, and deliberately the last statement in
# the module.
#
# Importing ``app.api.routes`` is what attaches this blueprint's eight view
# functions to ``api_bp``, and nothing else in the tree imports that module: the
# factory commits only to importing the blueprint objects themselves. Delete this
# line, or "tidy" it away as an unused import, and the application still starts,
# still registers a blueprint and still answers requests - but with a completely
# empty route map. That silent emptiness is the most easily missed failure in
# this package.
#
# It has to follow the assignment above rather than join the import at the top of
# the module: app/api/routes.py executes ``from app.api import api_bp``, which
# reads that name back off this partially initialised module through
# ``sys.modules``, so the name must already be bound when routes.py runs. A
# module-level import placed below other statements is exactly what E402 reports,
# hence the narrowly scoped suppression below - the only suppression in this file.
from app.api import routes as routes  # noqa: E402,F401
