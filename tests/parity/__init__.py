"""Executable parity evidence for the Java/Maven to Python 3 / Flask migration.

``tests.parity`` is THE ACCEPTANCE GATE of this migration. The directive behind
the rewrite was that it "fully matches the behavior and logic of the current
implementation"; the modules here are what turn that sentence into a checkable
fact rather than a claim. They measure the ported system against the three
artifacts that constitute the original implementation: ``README.md`` (the
behavioural specification), ``pom.xml`` (the dependency and execution contract,
held read-only and never compiled) and ``Jenkins`` (the orchestration contract).
This package is the sole realisation of engineering baseline item B11,
"Executable parity evidence rather than assertions of parity."

Inventory -- exactly five modules live here, this one included, and no sixth
module belongs in this directory.

``test_feature_file_byte_parity.py`` (criterion V1)
    Proves ``tests/features/login.feature`` is byte-identical to the Gherkin
    block documented in ``README.md``: 45 lines, 1864 bytes.

``test_dependency_parity.py`` (criteria V2 and V13)
    Proves every declared source dependency has a declared Python counterpart,
    and that every requirement line carries an exact ``==`` pin.

``test_defect_preservation.py`` (criterion V3)
    Proves defects D1, D4, D5 and D9 all still remain.

``test_default_run_exit_code.py`` (criterion V6)
    Proves the zero-selection default run reports SUCCESS.

Criteria V4 (the publisher constants) and V5 (the Cucumber JSON schema) are
named alongside these in the validation plan, but their canonical owners are
``tests/unit/test_thresholds_parity.py`` and
``tests/unit/test_cucumber_json_schema.py``; they are deliberately not
duplicated here.

THE COUNTER-INTUITIVE PROPERTY -- read this before "fixing" anything.

Transformation rule T4 governs every module in this package: "Defects are
behavior. The prompt requires the rewrite to 'fully match the behavior and logic
of the current implementation.' The specification contains nine verifiable
defects. Each is preserved as the default and made explicitly overridable
through configuration, never silently corrected."

The consequence is startling enough to state plainly: run exactly as documented,
the suite executes nothing. The ported runner keeps the documented tag
expression ``tags = "@LogOut"``; no scenario in the feature file carries that
tag; every scenario is therefore deselected -- and that zero-scenario run is a
SUCCESS, not an error, because the source build was non-gating too. Drop the tag
filter and six of the seven authored scenario instances execute while
``@UPGN-287`` is silently lost: its outline and the one following it parse to the
same scenario name, pytest-bdd derives the generated test-function name from the
scenario name, and the later definition simply overwrites the earlier. Each of
those outcomes is asserted here on purpose. The parity tests exist precisely so
this cannot later be mistaken for a porting bug.

Risk R3 states the price, which is accepted knowingly: "Preserving D1, D2, D4,
and D9 means the ported suite has almost no effective coverage by design.
tests/parity/test_defect_preservation.py asserts each preserved defect
explicitly, so the low coverage is provably intentional and cannot later be
mistaken for a porting failure."

Risk R6 explains the shape of every piece of evidence gathered here, and is
recorded openly rather than papered over: "The application under test is
external and unreachable from CI, so browser-driven scenarios cannot be executed
end to end here. Parity evidence is structural rather than end-to-end:
collection counts, artifact production, schema conformance, and constant
equality."

``docs/migration-parity.md`` is the authoritative register of defects D1 through
D9, each with its evidence, its preservation status and the configuration switch
that opts into a fix, per baseline item B12: "Every intentionally preserved
defect documented so it is auditable rather than accidental." That document
lists the three core modules of this package among its own dependencies, so the
relationship is bidirectional and deliberate -- a change on either side is
expected to be reflected on the other.

IMPORT HYGIENE -- the contract every module in this package honours.

* ``test_feature_file_byte_parity.py`` and ``test_dependency_parity.py`` use the
  standard library only and must run, and pass, with no third-party distribution
  installed at all. V1, V2 and V13 are deliberately the always-checkable
  criteria, so the gate still yields evidence in a bare environment.
* ``test_defect_preservation.py`` and ``test_default_run_exit_code.py`` guard
  their pytest-bdd-dependent parts with ``pytest.importorskip("pytest_bdd")``
  and keep every standard-library-checkable sub-assertion outside that guard, so
  partial evidence survives a partially provisioned environment.
* The ``app.*`` trap: reaching any module under ``app`` executes
  ``app/__init__.py`` first -- the Flask application factory -- so every such
  reference transitively requires Flask and needs the same guard.
* The stale-bytecode trap: an early verification of D9 appeared to DISPROVE the
  defect purely because a copied directory carried a stale bytecode cache, and
  re-running in a clean directory gave the correct result. ``__pycache__/`` and
  ``.pytest_cache/`` are git-ignored and wiped by ``make clean`` as correctness
  controls, not as cosmetics; any nested pytest run launched from these modules
  must use a clean temporary directory together with ``-p no:cacheprovider``.

THIS MODULE IS DELIBERATELY INERT. Its one job is to make ``tests.parity`` an
importable package so the four modules beside it are collectable, and it holds
no statement of any kind. Nothing is imported here, not even from the standard
library. There is no export list; no version constant, because packaging
metadata belongs to ``pyproject.toml`` alone and two sources would drift; no
re-export of anything from the sibling modules, from ``tests.pages``, from
``tests.support`` or from the application package; no module-level attribute
hook, since a PEP 562 lazy-import shim looks inert and is not -- it merely
defers the same failure to attribute-access time; no logger; no fixtures or
hooks; no plugin declaration; and no side effect of any sort. The only failure
mode of a package marker is doing too much: one convenience import here would
turn a single missing optional distribution into a collection error for this
whole package, which is to say for the project's entire acceptance gate.
Fixtures reach these modules through ordinary conftest inheritance from
``tests/conftest.py``; there is deliberately no conftest module in this
directory.
"""
