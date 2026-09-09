# Makefile - Testinium-QA (Python 3.14 port)
#
# Developer command surface for the ported project: four targets wrapping the
# `run-tests` console script and pytest. It stands in for the Maven command
# surface of the Java build - pom.xml is retained as historical reference only
# and is no longer a supported build configuration - so the semantics behind
# two of these targets come from there:
#   pom.xml:25     surefire testFailureIgnore=true  -> the runner exit contract
#                                                      described under `test`,
#                                                      which is reproduced by
#                                                      the runner and never
#                                                      suppressed here
#   pom.xml:21-29  surefire parallel configuration  -> the runner --workers
#                                                      default (CPU count)
#   `clean test`   the lifecycle developers invoked -> `make clean` plus the
#                                                      runner --clean default
#
# make is OPTIONAL developer convenience and must never become a CI
# dependency. The pipeline (Jenkins, stage "Run tests") selects
# scripts/run_tests.sh or scripts/run_tests.ps1 through its own isUnix()
# branch and never invokes make. Those two scripts are self-bootstrapping, and
# this file deliberately offers no bootstrap target for anything to depend on.
#
# Targets:
#   help      list the targets (default goal)
#   test      run the Gherkin browser suite through the run-tests entry point
#   unit      run the pytest suite covering this port's own code
#   coverage  run the four per-package coverage gates; first miss fails
#   clean     remove generated output and caches
#
# Recipe lines are indented with a literal TAB, which make requires.
#
# Absent on purpose: no bootstrap step the pipeline could come to depend on;
# no target that rebuilds report artifacts from stored results, which is not
# part of this port; no static-analysis or type-checking target, since no such
# tool is a pinned dependency (requirements-test.txt pins pytest and
# pytest-cov and nothing else); no container or deployment target; and no
# report artifact path literal, because those paths have exactly one owner,
# app/utils/paths.py. Removing the generated target/ directory wholesale is
# both the correct and the sufficient form.

# ---------------------------------------------------------------------------
# Interpreter and entry points
#
# The interpreter is Python 3.14.6 exactly, per .python-version and
# pyproject.toml (requires-python = "==3.14.*").
#
# Every command runs out of the project virtual environment explicitly rather
# than assuming an activated shell, and falls back to whatever the PATH
# provides so that an activated environment works too. The POSIX layout is the
# default; a Windows environment keeps its executables in Scripts, so point
# the location at it on the command line:
#     make unit VENV_BIN=.venv/Scripts
# (on Windows the pipeline runs scripts/run_tests.ps1, not make).
# ---------------------------------------------------------------------------
VENV      ?= .venv
VENV_BIN  ?= $(VENV)/bin

PYTHON    ?= $(if $(wildcard $(VENV_BIN)/python),$(VENV_BIN)/python,python3)
PYTEST    ?= $(PYTHON) -m pytest
RUN_TESTS ?= $(if $(wildcard $(VENV_BIN)/run-tests),$(VENV_BIN)/run-tests,run-tests)

# Extra options forwarded to the suite runner, e.g. `make test ARGS=--dry-run`.
ARGS      ?=

.DEFAULT_GOAL := help

.PHONY: help test unit coverage clean

help:
	@echo 'Testinium-QA developer targets (optional; CI calls scripts/run_tests.* directly)'
	@echo ''
	@echo '  make test      Run the Gherkin browser suite via the run-tests console script.'
	@echo '                 Runner defaults apply: --tags @Smoke, browser from'
	@echo '                 configuration.properties, --workers at CPU count, --clean on.'
	@echo '                 Forward extra options with ARGS, e.g. make test ARGS=--dry-run'
	@echo '  make unit      Run the pytest suite (test selection comes from pytest.ini).'
	@echo '  make coverage  Run the four per-package coverage gates; stops at the first miss.'
	@echo '  make clean     Remove the generated target/ tree and the Python and pytest caches.'
	@echo ''
	@echo 'Interpreter:  $(PYTHON)  (expected: Python 3.14.6)'
	@echo 'Suite runner: $(RUN_TESTS)'

# The Gherkin suite, through one entry point only: the `run-tests` console
# script declared in pyproject.toml under [project.scripts], taken from the
# virtual environment. Never the Flask CLI, never the BDD engine invoked
# directly, never `python -m app.cli`.
#
# No option value is hard-coded, so the runner defaults stay in force, and no
# status is suppressed - the recipe carries no leading dash and no fallback
# command that would discard a failing status. The runner already guarantees
# that a test outcome exits 0: scenario failures, errors, undefined or
# skipped steps, a browser that fails to start, an unknown browser value, a
# feature that fails to parse, a missing rerun manifest and an empty tag
# selection all exit 0, reproducing surefire
# testFailureIgnore=true (pom.xml:25) and the six -1 publisher thresholds in
# Jenkins. Suppressing status here would add nothing and would hide the three
# classes that must propagate: a usage error, a dead worker, and a merge or
# writer failure.
#
# A real run needs a browser and a populated configuration.properties; the
# repository supplies configuration.properties.example only.
test:
	$(RUN_TESTS) $(ARGS)

# This port's own unit suite. Plain and unmeasured: discovery, collection
# patterns and options all come from pytest.ini (testpaths = tests), so no
# path argument and no coverage flag is restated here. A non-zero status
# propagates - this is a real quality gate, the same one both runner scripts
# apply ahead of the browser run.
unit:
	$(PYTEST)

# The coverage gates, and the only place a threshold is declared anywhere in
# the project - pytest.ini holds test selection only. A single
# --cov-fail-under cannot express four different per-package thresholds, so
# pytest runs once per scope and each run measures and gates only its own
# package. Each line is its own shell and make stops at the first non-zero
# one, so the target fails on the first scope that misses. Four scopes
# exactly: no fifth scope, no aggregate threshold across app as a whole, and
# nothing here that could gate the plain `unit` target.
coverage:
	$(PYTEST) --cov=app/utils --cov-fail-under=90
	$(PYTEST) --cov=app/pages --cov-fail-under=85
	$(PYTEST) --cov=app/automation --cov-fail-under=80
	$(PYTEST) --cov=app/reporting --cov-fail-under=80

# Remove generated output only, tolerating its absence, so `make clean` on a
# fresh checkout succeeds and repeating it is harmless. The set is exactly
# what .gitignore excludes: the generated target/ tree, which holds the four
# report artifacts and the per-worker intermediates beneath it; the Python
# bytecode caches; the pytest cache; and the coverage data file.
#
# Never removed, which is why this recipe names paths instead of sweeping the
# tree: configuration.properties, a developer's local git-ignored credentials
# file whose deletion would be data loss; configuration.properties.example;
# the virtual environment; and anything tracked by git. The bytecode sweeps
# prune the git directory and the virtual environments for the same reason,
# and both remove through `-exec rm ... +` rather than through find's own
# delete action: that action forces depth-first traversal, which makes a
# prune list inoperative, and GNU find rejects the combination outright - so
# the shorter spelling would fail this target rather than protect anything.
#
# The runner has its own --clean/--no-clean option, on by default, which
# empties target/ before a run and is ignored under --rerun so that the
# manifest it reads survives. This target is the standalone equivalent of
# that option, not a replacement for it.
clean:
	rm -rf target .pytest_cache
	rm -f .coverage
	find . \( -name .git -o -name .venv -o -name venv \) -prune -o -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . \( -name .git -o -name .venv -o -name venv \) -prune -o -type f -name '*.pyc' -exec rm -f {} +
